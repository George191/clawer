import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  CheckCircleFilled,
  DatabaseOutlined,
  ReloadOutlined,
  SearchOutlined,
  TableOutlined,
  WarningFilled,
} from '@ant-design/icons';
import { Button, Empty, Input, Spin, theme } from 'antd';
import { useNavigate } from 'react-router-dom';
import ErrorBoundary from '@/components/ErrorBoundary';
import { fetchLayers, fetchLayerTables } from '@/services/api';
import type { LayerNode, LayerTable } from '@/services/types';
import '../ProductWorkspace/workspace.css';

const LAKE_ACCENT = '#10B981';
const ROW_COUNT_FORMATTER = new Intl.NumberFormat('zh-CN');

const formatRows = (count: number) => ROW_COUNT_FORMATTER.format(count);

const DataLake: React.FC = () => {
  const { token } = theme.useToken();
  const navigate = useNavigate();
  const [layers, setLayers] = useState<LayerNode[]>([]);
  const [selectedLayerKey, setSelectedLayerKey] = useState('');
  const [tables, setTables] = useState<LayerTable[]>([]);
  const [selectedTableName, setSelectedTableName] = useState('');
  const [query, setQuery] = useState('');
  const [layersLoading, setLayersLoading] = useState(true);
  const [tablesLoading, setTablesLoading] = useState(false);
  const [layerError, setLayerError] = useState('');
  const [tableError, setTableError] = useState('');
  const [tableRevision, setTableRevision] = useState(0);

  const loadLayers = useCallback(async () => {
    setLayersLoading(true);
    setLayerError('');
    try {
      const result = await fetchLayers();
      setLayers(result);
      setSelectedLayerKey((current) => (
        current && result.some((layer) => layer.key === current)
          ? current
          : result.find((layer) => layer.key === 'ods')?.key ?? result[0]?.key ?? ''
      ));
    } catch (reason) {
      setLayerError(reason instanceof Error ? reason.message : '无法读取数据湖目录');
    } finally {
      setLayersLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadLayers();
  }, [loadLayers]);

  useEffect(() => {
    if (!selectedLayerKey) {
      setTables([]);
      setSelectedTableName('');
      return;
    }

    let active = true;
    setTablesLoading(true);
    setTableError('');
    fetchLayerTables(selectedLayerKey)
      .then((result) => {
        if (!active) return;
        setTables(result);
        setSelectedTableName((current) => (
          current && result.some((table) => table.name === current)
            ? current
            : result[0]?.name ?? ''
        ));
      })
      .catch((reason: unknown) => {
        if (!active) return;
        setTables([]);
        setSelectedTableName('');
        setTableError(reason instanceof Error ? reason.message : '无法读取分层资产');
      })
      .finally(() => {
        if (active) setTablesLoading(false);
      });

    return () => {
      active = false;
    };
  }, [selectedLayerKey, tableRevision]);

  const handleRefresh = useCallback(async () => {
    await loadLayers();
    setTableRevision((revision) => revision + 1);
  }, [loadLayers]);

  const selectedLayer = useMemo(
    () => layers.find((layer) => layer.key === selectedLayerKey) ?? null,
    [layers, selectedLayerKey],
  );

  const visibleTables = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    if (!keyword) return tables;
    return tables.filter((table) => table.name.toLowerCase().includes(keyword));
  }, [query, tables]);

  const selectedTable = useMemo(
    () => tables.find((table) => table.name === selectedTableName) ?? null,
    [selectedTableName, tables],
  );

  const workspaceStyle = {
    '--workspace-accent': LAKE_ACCENT,
    '--workspace-bg': token.colorBgLayout,
    '--workspace-surface': token.colorBgContainer,
    '--workspace-surface-strong': token.colorBgElevated,
    '--workspace-border': token.colorBorder,
    '--workspace-border-soft': token.colorBorderSecondary,
    '--workspace-text': token.colorText,
    '--workspace-text-muted': token.colorTextSecondary,
    '--workspace-fill': token.colorFillAlter,
  } as React.CSSProperties;

  return (
    <ErrorBoundary>
      <main className="product-workspace" style={workspaceStyle} data-testid="data-lake-workspace">
        <header className="product-workspace__header">
          <div>
            <h1>数据资产工作台</h1>
            <p>从分层目录定位资产，确认规模、新鲜度与交付位置。</p>
          </div>
          <div className="product-workspace__header-actions">
            <span className="product-workspace__health">
              <span className="product-workspace__health-dot" />
              {layers.filter((layer) => layer.status === 'running').length}/{layers.length || 0} 层可用
            </span>
            <Button
              aria-label="刷新数据目录"
              icon={<ReloadOutlined />}
              onClick={() => void handleRefresh()}
              loading={layersLoading}
            >
              刷新
            </Button>
          </div>
        </header>

        <section className="product-command" aria-label="数据资产搜索">
          <SearchOutlined aria-hidden="true" />
          <Input
            variant="borderless"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索当前分层中的数据表"
            allowClear
          />
          <span className="product-command__hint">选择资产查看事实</span>
        </section>

        <section className="product-workspace__grid">
          <aside className="product-panel product-panel--rail" aria-label="数据湖分层">
            <div className="product-panel__heading">
              <span>湖仓分层</span>
              <span>{layers.length} 层</span>
            </div>
            {layersLoading ? (
              <div className="product-panel__state product-panel__state--compact"><Spin size="small" /></div>
            ) : layerError && layers.length === 0 ? (
              <div className="product-panel__state product-panel__state--compact product-panel__state--error">
                <WarningFilled />
                <span>{layerError}</span>
                <Button size="small" onClick={() => void loadLayers()}>重试</Button>
              </div>
            ) : (
              <div className="layer-rail" aria-label="数据湖分层列表">
                {layers.map((layer) => {
                  const selected = layer.key === selectedLayerKey;
                  return (
                    <button
                      type="button"
                      data-testid={`lake-layer-${layer.key}`}
                      key={layer.key}
                      className={`layer-rail__item${selected ? ' layer-rail__item--selected' : ''}`}
                      onClick={() => setSelectedLayerKey(layer.key)}
                      aria-pressed={selected}
                    >
                      <span className={`layer-rail__status layer-rail__status--${layer.status}`} />
                      <span className="layer-rail__name">
                        <strong>{layer.key.toUpperCase()}</strong>
                        <small>{layer.label.replace(`${layer.key.toUpperCase()} `, '')}</small>
                      </span>
                      <span className="layer-rail__count">{layer.tables ?? 0}</span>
                    </button>
                  );
                })}
              </div>
            )}
          </aside>

          <section className="product-panel product-panel--assets" aria-label="数据资产列表">
            <div className="product-panel__heading">
              <span>{selectedLayer ? `${selectedLayer.key.toUpperCase()} 数据资产` : '数据资产'}</span>
              <span>{visibleTables.length} 项</span>
            </div>
            <div className="asset-list__columns" aria-hidden="true">
              <span>资产名称</span><span>数据量</span><span>存储</span><span>更新时间</span>
            </div>
            {tablesLoading ? (
              <div className="product-panel__state"><Spin /><span>正在读取分层目录</span></div>
            ) : tableError ? (
              <div className="product-panel__state product-panel__state--error">
                <WarningFilled />
                <strong>分层目录不可用</strong>
                <span>{tableError}</span>
                <Button onClick={() => setTableRevision((revision) => revision + 1)}>重新加载</Button>
              </div>
            ) : visibleTables.length === 0 ? (
              <div className="product-panel__state">
                <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={query ? '没有匹配的数据资产' : '该分层尚未发现数据表'} />
              </div>
            ) : (
              <div className="asset-list" role="listbox" aria-label="数据资产">
                {visibleTables.map((table) => {
                  const selected = table.name === selectedTableName;
                  return (
                    <button
                      type="button"
                      role="option"
                      aria-selected={selected}
                      key={table.name}
                      className={`asset-row${selected ? ' asset-row--selected' : ''}`}
                      onClick={() => setSelectedTableName(table.name)}
                    >
                      <span className="asset-row__name">
                        <span className="asset-row__icon"><TableOutlined /></span>
                        <span><strong>{table.name}</strong><small>{selectedLayerKey}.{table.name}</small></span>
                      </span>
                      <span>{formatRows(table.rowCount)}</span>
                      <span>{table.size || '—'}</span>
                      <span>{table.updatedAt || '—'}</span>
                    </button>
                  );
                })}
              </div>
            )}
          </section>

          <aside className="product-panel product-panel--inspector" aria-label="资产检查器">
            <div className="product-panel__heading">
              <span>资产检查器</span>
              <span>目录事实</span>
            </div>
            {selectedTable && selectedLayer ? (
              <div className="inspector-content">
                <div className="inspector-title">
                  <span className="inspector-title__mark"><DatabaseOutlined /></span>
                  <div>
                    <strong>{selectedTable.name}</strong>
                    <span>{selectedLayer.key}.{selectedTable.name}</span>
                  </div>
                </div>
                <div className={`inspector-status inspector-status--${selectedLayer.status}`}>
                  <span>{selectedLayer.status === 'running' ? <CheckCircleFilled /> : <WarningFilled />} {selectedLayer.status === 'running' ? '资产可用' : '分层未运行'}</span>
                  <span>{selectedLayer.label}</span>
                </div>
                <dl className="inspector-facts">
                  <div><dt>记录数</dt><dd>{formatRows(selectedTable.rowCount)}</dd></div>
                  <div><dt>存储大小</dt><dd>{selectedTable.size || '—'}</dd></div>
                  <div><dt>最近更新</dt><dd>{selectedTable.updatedAt || '—'}</dd></div>
                  <div><dt>所属 Schema</dt><dd>{selectedLayer.key}</dd></div>
                </dl>
                <div className="lineage-mini" aria-label="数据分层路径">
                  {layers.map((layer) => (
                    <span key={layer.key} className={layer.key === selectedLayer.key ? 'lineage-mini__active' : ''}>
                      {layer.key.toUpperCase()}
                    </span>
                  ))}
                </div>
                <Button type="primary" block onClick={() => navigate('/explorer')}>
                  在分层浏览中打开
                </Button>
              </div>
            ) : (
              <div className="product-panel__state"><Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="请选择数据资产" /></div>
            )}
          </aside>
        </section>
      </main>
    </ErrorBoundary>
  );
};

export default DataLake;

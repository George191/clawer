import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  ApartmentOutlined,
  CheckCircleFilled,
  ReloadOutlined,
  SearchOutlined,
  WarningFilled,
} from '@ant-design/icons';
import { Button, Empty, Input, Spin, theme } from 'antd';
import ErrorBoundary from '@/components/ErrorBoundary';
import { fetchLayers } from '@/services/api';
import type { LayerNode } from '@/services/types';
import '../ProductWorkspace/workspace.css';

const PIPELINE_ACCENT = '#0EA5E9';

const PIPELINE_LAYOUT: Record<string, { x: number; y: number }> = {
  rds: { x: 9, y: 45 },
  ods: { x: 25, y: 45 },
  task: { x: 43, y: 45 },
  dwd: { x: 63, y: 24 },
  dws: { x: 63, y: 66 },
  dim: { x: 25, y: 82 },
  ads: { x: 86, y: 45 },
};

const statusLabel: Record<LayerNode['status'], string> = {
  running: '运行中',
  stopped: '未启动',
  error: '异常',
};

const layerShortName = (layer: LayerNode) => layer.key.toUpperCase();

const Pipeline: React.FC = () => {
  const { token } = theme.useToken();
  const [layers, setLayers] = useState<LayerNode[]>([]);
  const [selectedKey, setSelectedKey] = useState<string>('');
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const loadLayers = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const result = await fetchLayers();
      setLayers(result);
      setSelectedKey((current) => (
        current && result.some((layer) => layer.key === current)
          ? current
          : result.find((layer) => layer.status === 'running')?.key ?? result[0]?.key ?? ''
      ));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '无法读取 ETL 管道状态');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadLayers();
  }, [loadLayers]);

  const matchingLayerKeys = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    if (!keyword) return new Set(layers.map((layer) => layer.key));
    return new Set(
      layers
        .filter((layer) => layer.key.includes(keyword) || layer.label.toLowerCase().includes(keyword))
        .map((layer) => layer.key),
    );
  }, [layers, query]);

  const selectedLayer = useMemo(
    () => layers.find((layer) => layer.key === selectedKey) ?? null,
    [layers, selectedKey],
  );

  const runningCount = useMemo(
    () => layers.reduce((count, layer) => count + (layer.status === 'running' ? 1 : 0), 0),
    [layers],
  );

  const workspaceStyle = {
    '--workspace-accent': PIPELINE_ACCENT,
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
      <main className="product-workspace" style={workspaceStyle} data-testid="pipeline-workspace">
        <header className="product-workspace__header">
          <div>
            <h1>ETL 管道工作台</h1>
            <p>沿数据分层查看处理链路、运行状态与当前积压。</p>
          </div>
          <div className="product-workspace__header-actions">
            <span className="product-workspace__health">
              <span className="product-workspace__health-dot" />
              {runningCount}/{layers.length || 0} 层运行
            </span>
            <Button
              aria-label="刷新管道状态"
              icon={<ReloadOutlined />}
              onClick={() => void loadLayers()}
              loading={loading}
            >
              刷新
            </Button>
          </div>
        </header>

        <section className="product-command" aria-label="管道节点搜索">
          <SearchOutlined aria-hidden="true" />
          <Input
            variant="borderless"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索数据层或处理节点"
            allowClear
          />
          <span className="product-command__hint">选择节点查看运行事实</span>
        </section>

        <section className="product-workspace__grid product-workspace__grid--pipeline">
          <aside className="product-panel product-panel--rail" aria-label="管道概览">
            <div className="product-panel__heading">
              <span>主数据管道</span>
              <span>{layers.length} 层</span>
            </div>
            <button
              type="button"
              className="pipeline-run-card pipeline-run-card--active"
              aria-pressed="true"
            >
              <span className="pipeline-run-card__icon"><ApartmentOutlined /></span>
              <span>
                <strong>Lakehouse Main</strong>
                <small>采集入湖 · 标准化 · 交付</small>
              </span>
            </button>
            <div className="product-panel__section-label">运行事实</div>
            <dl className="fact-list">
              <div><dt>运行层</dt><dd>{runningCount}</dd></div>
              <div><dt>停止层</dt><dd>{layers.length - runningCount}</dd></div>
              <div><dt>总表数</dt><dd>{layers.reduce((sum, layer) => sum + (layer.tables ?? 0), 0)}</dd></div>
            </dl>
          </aside>

          <section className="product-panel product-panel--canvas" aria-label="ETL 管道拓扑">
            <div className="product-panel__heading">
              <span>处理链路</span>
              <span>RDS → ADS</span>
            </div>
            {loading ? (
              <div className="product-panel__state"><Spin /><span>正在读取管道状态</span></div>
            ) : error ? (
              <div className="product-panel__state product-panel__state--error">
                <WarningFilled />
                <strong>管道状态不可用</strong>
                <span>{error}</span>
                <Button onClick={() => void loadLayers()}>重新加载</Button>
              </div>
            ) : (
              <div className="pipeline-map" aria-label="ETL 处理节点">
                <svg className="pipeline-map__links" viewBox="0 0 1000 420" preserveAspectRatio="none" aria-hidden="true">
                  <defs>
                    <marker id="pipeline-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
                      <path d="M0 0L8 4L0 8Z" />
                    </marker>
                  </defs>
                  <path d="M130 189H210" />
                  <path d="M290 189H390" />
                  <path d="M475 175C525 175 540 101 590 101" />
                  <path d="M475 205C525 205 540 277 590 277" />
                  <path d="M680 101C755 101 760 174 820 186" />
                  <path d="M680 277C755 277 760 204 820 192" />
                  <path className="pipeline-map__link--aux" d="M250 225V330" />
                </svg>
                {layers.map((layer) => {
                  const selected = layer.key === selectedKey;
                  const matches = matchingLayerKeys.has(layer.key);
                  const position = PIPELINE_LAYOUT[layer.key] ?? { x: 50, y: 50 };
                  return (
                    <button
                      type="button"
                      key={layer.key}
                      data-testid={`pipeline-node-${layer.key}`}
                      className={`pipeline-node${selected ? ' pipeline-node--selected' : ''}${matches ? '' : ' pipeline-node--dimmed'}`}
                      style={{ left: `${position.x}%`, top: `${position.y}%` }}
                      onClick={() => setSelectedKey(layer.key)}
                      aria-pressed={selected}
                    >
                      <span className={`pipeline-node__status pipeline-node__status--${layer.status}`}>
                        {layer.status === 'running' ? <CheckCircleFilled /> : <WarningFilled />}
                      </span>
                      <span className="pipeline-node__code">{layerShortName(layer)}</span>
                      <strong>{layer.label.replace(`${layerShortName(layer)} `, '')}</strong>
                      <small>{layer.tables ?? 0} 张表</small>
                    </button>
                  );
                })}
                {query && matchingLayerKeys.size === 0 ? (
                  <div className="pipeline-map__no-match">没有匹配节点，拓扑保持完整显示</div>
                ) : null}
              </div>
            )}
            <div className="pipeline-canvas__legend">
              <span><i className="legend-dot legend-dot--running" />运行中</span>
              <span><i className="legend-dot legend-dot--stopped" />未启动</span>
              <span>点击节点切换检查器</span>
            </div>
          </section>

          <aside className="product-panel product-panel--inspector" aria-label="节点检查器">
            <div className="product-panel__heading">
              <span>节点检查器</span>
              <span>实时</span>
            </div>
            {selectedLayer ? (
              <div className="inspector-content">
                <div className="inspector-title">
                  <span className="inspector-title__mark">{layerShortName(selectedLayer).slice(0, 1)}</span>
                  <div>
                    <strong>{selectedLayer.label}</strong>
                    <span>{layerShortName(selectedLayer)} layer</span>
                  </div>
                </div>
                <div className={`inspector-status inspector-status--${selectedLayer.status}`}>
                  <span>{statusLabel[selectedLayer.status]}</span>
                  <span>{selectedLayer.status === 'running' ? '服务已发现表结构' : '等待层级表就绪'}</span>
                </div>
                <dl className="inspector-facts">
                  <div><dt>数据表</dt><dd>{selectedLayer.tables ?? 0}</dd></div>
                  <div><dt>处理速率</dt><dd>{selectedLayer.rate.toFixed(1)} msg/s</dd></div>
                  <div><dt>当前积压</dt><dd>{selectedLayer.lag}</dd></div>
                  <div><dt>Schema</dt><dd>{selectedLayer.key}</dd></div>
                </dl>
                <div className="inspector-note">
                  <strong>工作方式</strong>
                  <p>选择数据层后，检查器只展示后端返回的运行事实；处理器编辑与发布仍由对应工作区完成。</p>
                </div>
              </div>
            ) : (
              <div className="product-panel__state"><Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="请选择处理节点" /></div>
            )}
          </aside>
        </section>
      </main>
    </ErrorBoundary>
  );
};

export default Pipeline;

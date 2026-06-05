import React, { useState, useRef, useCallback, useEffect } from 'react';
import { Drawer, Button, Typography, theme, Card, Result, App } from 'antd';
import {
  MenuFoldOutlined,
  HistoryOutlined,
} from '@ant-design/icons';
import PageHeader from '@/components/PageHeader';
import ErrorBoundary from '@/components/ErrorBoundary';
import { executeQuery } from '@/services/api';
import type { QueryResult } from '@/services/types';
import LayerTree from './LayerTree';
import SqlEditor from './SqlEditor';
import ResultTable from './ResultTable';

const { Text } = Typography;

type HistoryItem = {
  sql: string;
  timestamp: string;
  elapsed?: number;
};

const DataExplorer: React.FC = () => {
  const { token } = theme.useToken();
  const { message: msgApi } = App.useApp();
  const [currentSql, setCurrentSql] = useState('SELECT * FROM ods.web_page LIMIT 50');
  const [result, setResult] = useState<QueryResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [execTime, setExecTime] = useState<number | null>(null);
  const [sqlError, setSqlError] = useState<string | null>(null);
  const [queryHistory, setQueryHistory] = useState<HistoryItem[]>([]);
  const [layerTreeVisible, setLayerTreeVisible] = useState(false);
  const [leftPanelWidth, setLeftPanelWidth] = useState(260);
  const [rightPanelWidth, setRightPanelWidth] = useState(240);

  // Responsive breakpoints
  const [windowWidth, setWindowWidth] = useState(window.innerWidth);
  useEffect(() => {
    const handleResize = () => setWindowWidth(window.innerWidth);
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  const isMobile = windowWidth < 768;
  const hideHistory = windowWidth < 1200;

  const handleExecute = useCallback(async (sql: string) => {
    setCurrentSql(sql);
    setSqlError(null);
    setLoading(true);
    setExecTime(null);

    const start = performance.now();
    try {
      const res = await executeQuery(sql);
      const duration = Math.round(performance.now() - start);
      setResult(res);
      setExecTime(duration);
      setQueryHistory((prev) => [
        { sql, timestamp: new Date().toLocaleTimeString('zh-CN', { hour12: false }), elapsed: res.elapsed },
        ...prev.slice(0, 49),
      ]);
    } catch (e: unknown) {
      const err = e as { message?: string };
      setSqlError(err?.message || '查询执行失败，请检查 SQL 语法');
      setResult(null);
    } finally {
      setLoading(false);
    }
  }, []);

  const handleInsertTable = useCallback((tableRef: string) => {
    setCurrentSql((prev) => prev.trimEnd() + ` ${tableRef}`);
  }, []);

  const handleDoubleClickTable = useCallback((tableRef: string) => {
    setCurrentSql(`SELECT * FROM ${tableRef} LIMIT 100;`);
  }, []);

  // Drag resize for left panel
  const handleLeftResize = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    const startX = e.clientX;
    const startW = leftPanelWidth;
    const onMove = (ev: MouseEvent) => setLeftPanelWidth(Math.max(200, Math.min(500, startW + ev.clientX - startX)));
    const onUp = () => { document.removeEventListener('mousemove', onMove); document.removeEventListener('mouseup', onUp); };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  }, [leftPanelWidth]);

  // Drag resize for right panel
  const handleRightResize = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    const startX = e.clientX;
    const startW = rightPanelWidth;
    const onMove = (ev: MouseEvent) => setRightPanelWidth(Math.max(180, Math.min(400, startW - ev.clientX + startX)));
    const onUp = () => { document.removeEventListener('mousemove', onMove); document.removeEventListener('mouseup', onUp); };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  }, [rightPanelWidth]);

  return (
    <ErrorBoundary>
      <PageHeader
        title="数据探索"
        extra={
          isMobile ? (
            <Button icon={<MenuFoldOutlined />} onClick={() => setLayerTreeVisible(true)}>
              层级树
            </Button>
          ) : null
        }
      />

      <div style={{ display: 'flex', gap: 0, height: 'calc(100vh - 140px)', minHeight: 600 }}>
        {/* Left: LayerTree */}
        {!isMobile && (
          <>
            <div style={{ width: leftPanelWidth, flexShrink: 0, overflow: 'auto' }}>
              <LayerTree
                onInsertTable={handleInsertTable}
                onDoubleClickTable={handleDoubleClickTable}
              />
            </div>
            <div
              onMouseDown={handleLeftResize}
              style={{
                width: 4, cursor: 'col-resize', flexShrink: 0,
                background: token.colorBorderSecondary, transition: 'background 0.2s',
              }}
              onMouseEnter={(e) => (e.currentTarget.style.background = token.colorPrimary)}
              onMouseLeave={(e) => (e.currentTarget.style.background = token.colorBorderSecondary)}
            />
          </>
        )}

        {/* Center: Editor + Results */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0, padding: '0 12px' }}>
          <SqlEditor
            value={currentSql}
            onChange={setCurrentSql}
            onExecute={handleExecute}
            error={sqlError}
            execTime={execTime}
          />
          <ResultTable result={result} loading={loading} />
        </div>

        {/* Right: Query History */}
        {!hideHistory && (
          <>
            <div
              onMouseDown={handleRightResize}
              style={{
                width: 4, cursor: 'col-resize', flexShrink: 0,
                background: token.colorBorderSecondary, transition: 'background 0.2s',
              }}
              onMouseEnter={(e) => (e.currentTarget.style.background = token.colorPrimary)}
              onMouseLeave={(e) => (e.currentTarget.style.background = token.colorBorderSecondary)}
            />
            <div style={{ width: rightPanelWidth, flexShrink: 0, overflow: 'auto' }}>
              <Card
                size="small"
                title={<span><HistoryOutlined style={{ marginRight: 6 }} />查询历史</span>}
                style={{ height: '100%' }}
                bodyStyle={{ padding: '8px 12px' }}
              >
                {queryHistory.length === 0 ? (
                  <Text type="secondary" style={{ fontSize: 12, display: 'block', textAlign: 'center', padding: 24 }}>
                    暂无查询历史
                  </Text>
                ) : (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                    {queryHistory.map((item, idx) => (
                      <div
                        key={idx}
                        onClick={() => setCurrentSql(item.sql)}
                        style={{
                          padding: '8px 10px', borderRadius: 6, cursor: 'pointer', transition: 'all 0.2s',
                          border: `1px solid ${token.colorBorderSecondary}`, background: token.colorBgContainer,
                        }}
                        onMouseEnter={(e) => { e.currentTarget.style.borderColor = token.colorPrimary; e.currentTarget.style.background = token.colorFillAlter; }}
                        onMouseLeave={(e) => { e.currentTarget.style.borderColor = token.colorBorderSecondary; e.currentTarget.style.background = token.colorBgContainer; }}
                      >
                        <div style={{ fontSize: 11, color: token.colorTextTertiary, marginBottom: 3, display: 'flex', justifyContent: 'space-between' }}>
                          <span>{item.timestamp}</span>
                          {item.elapsed != null && <span>{item.elapsed}s</span>}
                        </div>
                        <div
                          style={{
                            fontSize: 12, fontFamily: '"Fira Code", "Cascadia Code", monospace',
                            color: token.colorText, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                          }}
                        >
                          {item.sql}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </Card>
            </div>
          </>
        )}
      </div>

      {/* Mobile LayerTree Drawer */}
      <Drawer title="ETL 层级" open={layerTreeVisible} onClose={() => setLayerTreeVisible(false)} width={280} placement="left">
        <LayerTree
          onInsertTable={(t) => { handleInsertTable(t); setLayerTreeVisible(false); }}
          onDoubleClickTable={(t) => { handleDoubleClickTable(t); setLayerTreeVisible(false); }}
        />
      </Drawer>
    </ErrorBoundary>
  );
};

export default DataExplorer;
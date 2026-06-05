import React, { useState, useCallback } from 'react';
import { Drawer, Grid } from 'antd';
import PageHeader from '@/components/PageHeader';
import ErrorBoundary from '@/components/ErrorBoundary';
import PipelineDAG from './PipelineDAG';
import HandlerEditor from './HandlerEditor';

const Pipeline: React.FC = () => {
  const [selectedNode, setSelectedNode] = useState<string | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const screens = Grid.useBreakpoint();
  const isMobile = !screens.lg; // < 1024px

  const handleNodeClick = useCallback(
    (layer: string) => {
      setSelectedNode(layer);
      if (isMobile) setDrawerOpen(true);
    },
    [isMobile],
  );

  const handleDrawerClose = useCallback(() => {
    setDrawerOpen(false);
  }, []);

  return (
    <ErrorBoundary>
      <PageHeader title="管道管理" />
      <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start' }}>
        {/* 左侧 DAG 图 */}
        <div style={{ flex: 2, minWidth: 0 }}>
          <PipelineDAG onNodeClick={handleNodeClick} selectedNode={selectedNode} />
        </div>

        {/* 右侧节点信息面板（≥1024px 显示） */}
        {!isMobile && (
          <div style={{ width: 400, flexShrink: 0 }}>
            <HandlerEditor selectedNode={selectedNode} />
          </div>
        )}
      </div>

      {/* 小屏：Drawer 中显示 */}
      {isMobile && (
        <Drawer
          title="节点详情"
          placement="right"
          width={400}
          open={drawerOpen}
          onClose={handleDrawerClose}
          destroyOnClose={false}
        >
          <HandlerEditor selectedNode={selectedNode} />
        </Drawer>
      )}
    </ErrorBoundary>
  );
};

export default Pipeline;
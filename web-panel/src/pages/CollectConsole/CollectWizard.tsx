import React from 'react';
import { Drawer } from 'antd';
import { aiAura } from './shared/aura';

interface Props {
  open: boolean;
  onClose: () => void;
}

/**
 * CollectWizard — 智能采集向导覆盖层
 *
 * 三合一合并方案中的核心：将原 AICollect 页面作为全屏 Drawer 覆盖层嵌入，
 * 保留 SSE 流式分析、6 步流程、终端日志等完整功能。
 * 用户从工作台的「智能采集」按钮触发。
 */
const CollectWizard: React.FC<Props> = ({ open, onClose }) => {
  return (
    <>
      <style>{`
        .cc-wizard .ant-drawer-content {
          background: ${aiAura.bg};
          color: ${aiAura.text};
        }
        .cc-wizard .ant-drawer-header {
          background: ${aiAura.bg};
          border-bottom-color: ${aiAura.border};
        }
        .cc-wizard .ant-drawer-title,
        .cc-wizard .ant-drawer-close {
          color: ${aiAura.text};
        }
        .cc-wizard .ant-drawer-body {
          padding: 0;
          overflow: hidden;
        }
        .cc-wizard-content {
          height: 100%;
          overflow: auto;
        }
      `}</style>

      <Drawer
        className="cc-wizard"
        placement="right"
        width="100%"
        open={open}
        onClose={onClose}
        title="智能采集向导"
        footer={null}
        destroyOnClose={false}
      >
        <div className="cc-wizard-content">
          {/* 延迟加载 AICollect 组件，避免首屏负担 */}
          <React.Suspense fallback={
            <div style={{
              height: '100%',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: aiAura.subtle,
            }}>
              加载中…
            </div>
          }>
            {open && <LazyAICollect />}
          </React.Suspense>
        </div>
      </Drawer>
    </>
  );
};

// 延迟加载 AICollect 组件
const LazyAICollect = React.lazy(() => import('@/pages/AICollect'));

export default CollectWizard;

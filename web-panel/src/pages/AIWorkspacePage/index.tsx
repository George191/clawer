import React from 'react';
import { useNavigate } from 'react-router-dom';
import WorkspaceDock, { type WorkspacePanel } from '@/pages/AICollect/WorkspaceDock';

interface AIWorkspacePageProps {
  panel: WorkspacePanel;
}

const AIWorkspacePage: React.FC<AIWorkspacePageProps> = ({ panel }) => {
  const navigate = useNavigate();

  return (
    <div className="ai-workspace-page">
      <style>{`
        body:has(.ai-workspace-page) .ant-layout-content {
          background:
            radial-gradient(ellipse at 50% 38%, rgba(44, 72, 151, 0.36) 0%, rgba(28, 47, 103, 0.18) 34%, rgba(23, 26, 26, 0) 64%),
            linear-gradient(180deg, #101212 0%, #171B24 58%, #141818 100%) !important;
        }
      `}</style>
      <WorkspaceDock
        activePanel={panel}
        onToggle={(nextPanel) => navigate(nextPanel === 'templates' ? '/templates' : '/tasks')}
        onClose={() => navigate('/ai-collect')}
      />
    </div>
  );
};

export default AIWorkspacePage;

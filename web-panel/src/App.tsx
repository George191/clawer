import { Routes, Route, Navigate } from 'react-router-dom';
import { ConfigProvider, App as AntApp, theme as antTheme, message } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import MainLayout from '@/layouts/MainLayout';
import { useThemeStore } from '@/stores/settings';
import { themeTokens } from './theme/tokens';
import { darkTheme } from './theme/dark';
import { lightTheme } from './theme/light';

import CommandCenter from '@/pages/CommandCenter';
import Dashboard from '@/pages/Dashboard';
import DataExplorer from '@/pages/DataExplorer';
import TaskCenter from '@/pages/TaskCenter';
import Monitoring from '@/pages/Monitoring';
import Pipeline from '@/pages/Pipeline';
import Templates from '@/pages/Templates';
import AICollect from '@/pages/AICollect';
import AICollectGovernance from '@/pages/AICollectGovernance';
import LogExplorer from '@/pages/LogExplorer';
import WorkspacePage from '@/pages/WorkspacePage';

const App: React.FC = () => {
  const { mode } = useThemeStore();

  const isDark = mode === 'dark';
  const currentToken = isDark ? darkTheme : lightTheme;

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const mergedToken = { ...themeTokens, ...currentToken } as any;

  message.config({
    top: 84,
    duration: 2.2,
    maxCount: 3,
    rtl: false,
  });

  return (
    <ConfigProvider
      locale={zhCN}
      theme={{
        algorithm: isDark ? antTheme.darkAlgorithm : antTheme.defaultAlgorithm,
        token: mergedToken,
      }}
    >
      <AntApp>
        <MainLayout>
          <Routes>
            <Route path="/" element={<CommandCenter />} />
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/explorer" element={<DataExplorer />} />
            <Route path="/tasks" element={<TaskCenter />} />
            <Route path="/monitor" element={<Monitoring />} />
            <Route path="/logs" element={<LogExplorer />} />
            <Route path="/pipeline" element={<Pipeline />} />
            <Route path="/pipeline/schedule" element={<WorkspacePage />} />
            <Route path="/pipeline/releases" element={<WorkspacePage />} />
            <Route path="/pipeline/alerts" element={<WorkspacePage />} />
            <Route path="/templates" element={<Templates />} />
            <Route path="/ai-collect" element={<AICollect />} />
            <Route path="/source-strategy" element={<AICollectGovernance />} />
            <Route path="/anti-crawl" element={<AICollectGovernance />} />
            <Route path="/field-mapping" element={<AICollectGovernance />} />
            <Route path="/lake/catalog" element={<WorkspacePage />} />
            <Route path="/lake/metadata" element={<WorkspacePage />} />
            <Route path="/lake/quality" element={<WorkspacePage />} />
            <Route path="/lake/lineage" element={<WorkspacePage />} />
            <Route path="/lake/security" element={<WorkspacePage />} />
            <Route path="/lake/market" element={<WorkspacePage />} />
            <Route path="/data-api" element={<WorkspacePage />} />
            {/* Legacy sidebar routes */}
            <Route path="/instances" element={<WorkspacePage />} />
            <Route path="/import" element={<AICollect />} />
            <Route path="/graph-analytics" element={<WorkspacePage />} />
            <Route path="/explore" element={<DataExplorer />} />
            <Route path="/dashboards" element={<Dashboard />} />
            <Route path="/query" element={<DataExplorer />} />
            <Route path="/metrics" element={<Monitoring />} />
            <Route path="/project-users" element={<WorkspacePage />} />
            <Route path="/billing" element={<WorkspacePage />} />
            <Route path="/project-settings" element={<WorkspacePage />} />
            <Route path="/learning" element={<WorkspacePage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </MainLayout>
      </AntApp>
    </ConfigProvider>
  );
};

export default App;

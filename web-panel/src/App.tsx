import { Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { ConfigProvider, App as AntApp, theme as antTheme, message } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import enUS from 'antd/locale/en_US';
import MainLayout from '@/layouts/MainLayout';
import { useThemeStore } from '@/stores/settings';
import { themeTokens } from './theme/tokens';
import { darkTheme } from './theme/dark';
import { lightTheme } from './theme/light';

import Dashboard from '@/pages/Dashboard';
import DataLake from '@/pages/DataLake';
import DataExplorer from '@/pages/DataExplorer';
import Monitoring from '@/pages/Monitoring';
import Pipeline from '@/pages/Pipeline';
import AICollect from '@/pages/AICollect';
import AICollectGovernance from '@/pages/AICollectGovernance';
import LogExplorer from '@/pages/LogExplorer';
import WorkspacePage from '@/pages/WorkspacePage';
import Login from '@/pages/Login';
import ResetPassword from '@/pages/Login/ResetPassword';
import DataGraph from '@/pages/DataGraph';
import Organization from '@/pages/Organization';
import AutomationCenter from '@/pages/AutomationCenter';
import AccountSettings from '@/pages/AccountSettings';
import SystemSettings from '@/pages/SystemSettings';
import FAQ from '@/pages/FAQ';
import Legal from '@/pages/Legal';
import AuthLayout from '@/pages/Login/AuthLayout';

const App: React.FC = () => {
  const { mode, language } = useThemeStore();
  const location = useLocation();

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
      locale={language === 'en-US' ? enUS : zhCN}
      theme={{
        algorithm: isDark ? antTheme.darkAlgorithm : antTheme.defaultAlgorithm,
        token: mergedToken,
      }}
    >
      <AntApp>
        {['/login', '/reset-password'].includes(location.pathname) ? (
          <AuthLayout>
            <Routes>
              <Route path="/login" element={<Login />} />
              <Route path="/reset-password" element={<ResetPassword />} />
            </Routes>
          </AuthLayout>
        ) : (
          <MainLayout>
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/atlas" element={<Dashboard />} />
            <Route path="/atlas/metrics" element={<Monitoring />} />
            <Route path="/atlas/quality" element={<WorkspacePage />} />
            <Route path="/explorer" element={<DataExplorer />} />
            <Route path="/tasks" element={<Navigate to="/automation/runs" replace />} />
            <Route path="/monitor" element={<Monitoring />} />
            <Route path="/logs" element={<LogExplorer />} />
            <Route path="/flow" element={<Navigate to="/flow/layers" replace />} />
            <Route path="/flow/runs" element={<Navigate to="/automation/runs?domain=etl-pipeline" replace />} />
            <Route path="/flow/schedules" element={<Navigate to="/automation/schedules?domain=etl-pipeline" replace />} />
            <Route path="/flow/layers" element={<Pipeline />} />
            <Route path="/flow/transforms" element={<Pipeline />} />
            <Route path="/flow/quality" element={<Pipeline />} />
            <Route path="/flow/lineage" element={<Pipeline />} />
            <Route path="/flow/tasks" element={<Navigate to="/automation/runs?domain=etl-pipeline" replace />} />
            <Route path="/flow/templates" element={<Navigate to="/automation/workflows?domain=etl-pipeline" replace />} />
            <Route path="/flow/schedule" element={<Navigate to="/automation/schedules?domain=etl-pipeline" replace />} />
            <Route path="/automation" element={<Navigate to="/automation/workflows" replace />} />
            <Route path="/automation/workflows" element={<AutomationCenter />} />
            <Route path="/automation/schedules" element={<AutomationCenter />} />
            <Route path="/automation/runs" element={<AutomationCenter />} />
            <Route path="/scout" element={<AICollect />} />
            <Route path="/source-strategy" element={<AICollectGovernance />} />
            <Route path="/anti-crawl" element={<AICollectGovernance />} />
            <Route path="/field-mapping" element={<AICollectGovernance />} />
            <Route path="/vault/catalog" element={<DataLake />} />
            <Route path="/vault/metadata" element={<WorkspacePage />} />
            <Route path="/vault/quality" element={<WorkspacePage />} />
            <Route path="/vault/lineage" element={<DataGraph />} />
            <Route path="/vault/security" element={<WorkspacePage />} />
            <Route path="/vault/market" element={<WorkspacePage />} />
            <Route path="/data-api" element={<WorkspacePage />} />
            <Route path="/graph" element={<WorkspacePage />} />
            <Route path="/graph/entities" element={<WorkspacePage />} />
            <Route path="/graph/quality" element={<WorkspacePage />} />
            <Route path="/graph/lineage" element={<WorkspacePage />} />
            <Route path="/rag" element={<WorkspacePage />} />
            <Route path="/rag/documents" element={<WorkspacePage />} />
            <Route path="/rag/indexes" element={<WorkspacePage />} />
            <Route path="/rag/evaluation" element={<WorkspacePage />} />
            {/* Legacy sidebar routes */}
            <Route path="/instances" element={<WorkspacePage />} />
            <Route path="/import" element={<Navigate to="/scout" replace />} />
            <Route path="/graph-analytics" element={<DataGraph />} />
            <Route path="/explore" element={<DataExplorer />} />
            <Route path="/dashboards" element={<Dashboard />} />
            <Route path="/query" element={<DataExplorer />} />
            <Route path="/metrics" element={<Monitoring />} />
            <Route path="/organization" element={<Organization />} />
            <Route path="/organization/users/:userId" element={<Organization />} />
            <Route path="/account/settings" element={<AccountSettings />} />
            <Route path="/settings" element={<SystemSettings />} />
            <Route path="/faq" element={<FAQ />} />
            <Route path="/legal/:section" element={<Legal />} />
            <Route path="/project-users" element={<Organization />} />
            <Route path="/billing" element={<WorkspacePage />} />
            <Route path="/project-settings" element={<WorkspacePage />} />
            <Route path="/learning" element={<WorkspacePage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
          </MainLayout>
        )}
      </AntApp>
    </ConfigProvider>
  );
};

export default App;

import { lazy, Suspense } from 'react';
import { Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { ConfigProvider, App as AntApp, theme as antTheme, message, Spin } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import enUS from 'antd/locale/en_US';
import { useThemeStore } from '@/stores/settings';
import { themeTokens } from './theme/tokens';
import { darkTheme } from './theme/dark';
import { lightTheme } from './theme/light';

import Login from '@/pages/Login';
import ResetPassword from '@/pages/Login/ResetPassword';
import AuthLayout from '@/pages/Login/AuthLayout';

const MainLayout = lazy(() => import('@/layouts/MainLayout'));
const Dashboard = lazy(() => import('@/pages/Dashboard'));
const DataLake = lazy(() => import('@/pages/DataLake'));
const DataExplorer = lazy(() => import('@/pages/DataExplorer'));
const Monitoring = lazy(() => import('@/pages/Monitoring'));
const Pipeline = lazy(() => import('@/pages/Pipeline'));
const AICollect = lazy(() => import('@/pages/AICollect'));
const AICollectGovernance = lazy(() => import('@/pages/AICollectGovernance'));
const LogExplorer = lazy(() => import('@/pages/LogExplorer'));
const WorkspacePage = lazy(() => import('@/pages/WorkspacePage'));
const DataGraph = lazy(() => import('@/pages/DataGraph'));
const Organization = lazy(() => import('@/pages/Organization'));
const AutomationCenter = lazy(() => import('@/pages/AutomationCenter'));
const AccountSettings = lazy(() => import('@/pages/AccountSettings'));
const SystemSettings = lazy(() => import('@/pages/SystemSettings'));
const FAQ = lazy(() => import('@/pages/FAQ'));
const Legal = lazy(() => import('@/pages/Legal'));

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
        ) : !localStorage.getItem('access_token') && !localStorage.getItem('refresh_token') ? (
          <Navigate to={`/login?next=${encodeURIComponent(location.pathname + location.search + location.hash)}`} replace />
        ) : (
          <Suspense fallback={<div role="status" style={{ minHeight: '100dvh', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 12 }}><Spin /><span>正在加载页面...</span></div>}>
          <MainLayout>
          <Suspense fallback={<div role="status" style={{ minHeight: 240, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 12 }}><Spin /><span>正在加载页面...</span></div>}>
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
            <Route path="/flow" element={<Pipeline />} />
            <Route path="/flow/layers" element={<Pipeline />} />
            <Route path="/flow/runs" element={<Navigate to="/automation/runs?domain=etl-pipeline" replace />} />
            <Route path="/flow/schedules" element={<Navigate to="/automation/schedules?domain=etl-pipeline" replace />} />
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
          </Suspense>
          </MainLayout>
          </Suspense>
        )}
      </AntApp>
    </ConfigProvider>
  );
};

export default App;

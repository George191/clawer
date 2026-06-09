import { Routes, Route, Navigate } from 'react-router-dom';
import { ConfigProvider, App as AntApp, theme as antTheme } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import MainLayout from '@/layouts/MainLayout';
import { useThemeStore } from '@/stores/settings';
import { themeTokens } from './theme/tokens';
import { darkTheme } from './theme/dark';
import { lightTheme } from './theme/light';

import Dashboard from '@/pages/Dashboard';
import DataExplorer from '@/pages/DataExplorer';
import TaskCenter from '@/pages/TaskCenter';
import Monitoring from '@/pages/Monitoring';
import Pipeline from '@/pages/Pipeline';
import Templates from '@/pages/Templates';
import AICollect from '@/pages/AICollect';

const App: React.FC = () => {
  const { mode } = useThemeStore();

  const isDark = mode === 'dark';
  const currentToken = isDark ? darkTheme : lightTheme;

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const mergedToken = { ...themeTokens, ...currentToken } as any;

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
            <Route path="/" element={<Dashboard />} />
            <Route path="/explorer" element={<DataExplorer />} />
            <Route path="/tasks" element={<TaskCenter />} />
            <Route path="/monitor" element={<Monitoring />} />
            <Route path="/pipeline" element={<Pipeline />} />
            <Route path="/templates" element={<Templates />} />
            <Route path="/ai-collect" element={<AICollect />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </MainLayout>
      </AntApp>
    </ConfigProvider>
  );
};

export default App;
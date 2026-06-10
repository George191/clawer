import { Routes, Route, Navigate } from 'react-router-dom';
import { ConfigProvider, App as AntApp, theme as antTheme } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import MainLayout from '@/layouts/MainLayout';
import { useThemeStore } from '@/stores/settings';

import Dashboard from '@/pages/Dashboard';
import DataExplorer from '@/pages/DataExplorer';
import TaskCenter from '@/pages/TaskCenter';
import Monitoring from '@/pages/Monitoring';
import Pipeline from '@/pages/Pipeline';
import Templates from '@/pages/Templates';
import AICollect from '@/pages/AICollect';

/**
 * Ant Design ConfigProvider 主题映射
 *
 * 所有 token 值引用 CSS 变量 --theme-*：
 * 组件只使用 var(--theme-*) 语义变量，由 CSS 决定暗色/亮色实际色值。
 */
const antdThemeConfig = {
  token: {
    // ── 主色 ──
    colorPrimary: 'var(--theme-color-primary-text)',
    colorPrimaryHover: 'var(--theme-color-primary-hover-strong)',
    colorPrimaryActive: 'var(--theme-color-primary-pressed-strong)',
    colorPrimaryBg: 'var(--theme-color-primary-bg-weak)',
    colorPrimaryBgHover: 'var(--theme-color-primary-hover-weak)',
    colorPrimaryBorder: 'var(--theme-color-primary-border-weak)',
    colorPrimaryBorderHover: 'var(--theme-color-primary-border-strong)',
    colorPrimaryText: 'var(--theme-color-primary-text)',
    colorPrimaryTextHover: 'var(--theme-color-primary-hover-strong)',
    colorPrimaryTextActive: 'var(--theme-color-primary-pressed-strong)',

    // ── 背景 ──
    colorBgBase: 'var(--theme-color-neutral-bg-default)',
    colorBgContainer: 'var(--theme-color-neutral-bg-weak)',
    colorBgElevated: 'var(--theme-color-neutral-bg-weak)',
    colorBgLayout: 'var(--theme-color-neutral-bg-default)',
    colorBgSpotlight: 'var(--theme-color-neutral-bg-strong)',
    colorBgMask: 'rgba(0, 0, 0, 0.45)',

    // ── 文字 ──
    colorTextBase: 'var(--theme-color-neutral-text-default)',
    colorText: 'var(--theme-color-neutral-text-default)',
    colorTextSecondary: 'var(--theme-color-neutral-text-weak)',
    colorTextTertiary: 'var(--theme-color-neutral-text-weaker)',
    colorTextQuaternary: 'var(--theme-color-neutral-text-weakest)',

    // ── 边框 ──
    colorBorder: 'var(--theme-color-neutral-border-weak)',
    colorBorderSecondary: 'var(--theme-color-neutral-border-strong)',

    // ── 填充 ──
    colorFill: 'var(--theme-color-neutral-hover)',
    colorFillSecondary: 'var(--theme-color-neutral-hover)',
    colorFillTertiary: 'var(--theme-color-neutral-hover)',
    colorFillQuaternary: 'rgba(0, 0, 0, 0.01)',
    colorFillAlter: 'var(--theme-color-neutral-bg-strong)',
    colorFillContent: 'var(--theme-color-neutral-bg-strong)',
    colorFillContentHover: 'var(--theme-color-neutral-hover)',

    // ── 状态色 ──
    colorSuccess: 'var(--theme-color-success-text)',
    colorSuccessHover: 'var(--theme-color-success-bg-status)',
    colorSuccessActive: 'var(--theme-color-success-border-strong)',
    colorSuccessBg: 'var(--theme-color-success-bg-weak)',
    colorSuccessBorder: 'var(--theme-color-success-border-weak)',
    colorSuccessText: 'var(--theme-color-success-text)',

    colorWarning: 'var(--theme-color-warning-text)',
    colorWarningHover: 'var(--theme-color-warning-bg-status)',
    colorWarningActive: 'var(--theme-color-warning-border-strong)',
    colorWarningBg: 'var(--theme-color-warning-bg-weak)',
    colorWarningBorder: 'var(--theme-color-warning-border-weak)',
    colorWarningText: 'var(--theme-color-warning-text)',

    colorError: 'var(--theme-color-danger-text)',
    colorErrorHover: 'var(--theme-color-danger-bg-status)',
    colorErrorActive: 'var(--theme-color-danger-border-strong)',
    colorErrorBg: 'var(--theme-color-danger-bg-weak)',
    colorErrorBorder: 'var(--theme-color-danger-border-weak)',
    colorErrorText: 'var(--theme-color-danger-text)',

    colorInfo: 'var(--theme-color-discovery-text)',
    colorInfoHover: 'var(--theme-color-discovery-bg-status)',
    colorInfoActive: 'var(--theme-color-discovery-border-strong)',
    colorInfoBg: 'var(--theme-color-discovery-bg-weak)',
    colorInfoBorder: 'var(--theme-color-discovery-border-weak)',
    colorInfoText: 'var(--theme-color-discovery-text)',

    // ── 控件 ──
    controlItemBgHover: 'var(--theme-color-neutral-hover)',
    controlItemBgActive: 'var(--theme-color-neutral-pressed)',
    controlHeight: 32,
    controlHeightLG: 40,
    controlHeightSM: 24,
    controlOutline: 'var(--theme-color-primary-focus)',

    // ── 圆角 ──
    borderRadius: 6,
    borderRadiusLG: 8,
    borderRadiusSM: 4,

    // ── 字体 ──
    fontSize: 14,
    fontFamily: "var(--theme-font-body)",
    lineHeight: 1.429,

    // ── 阴影 ──
    boxShadow: 'var(--theme-shadow-raised)',
    boxShadowSecondary: 'var(--theme-shadow-overlay)',

    // ── 间距 ──
    paddingLG: 24,
    paddingMD: 16,
    paddingSM: 12,
    paddingXS: 8,
    marginLG: 24,
    marginMD: 16,
    marginSM: 12,
    marginXS: 8,

    wireframe: false,
  },
};

const App: React.FC = () => {
  const { mode } = useThemeStore();
  const isDark = mode === 'dark' ||
    (mode === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches);

  return (
    <ConfigProvider
      locale={zhCN}
      theme={{
        algorithm: isDark ? antTheme.darkAlgorithm : antTheme.defaultAlgorithm,
        ...antdThemeConfig,
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

import type { FullToken } from './tokens';

/**
 * 暗色主题 Token
 *
 * 设计思路：
 * - 深色背景渐变 #0A0E17 → #111827
 * - 半透明卡片容器，毛玻璃质感
 * - colorPrimary: 宝石蓝 #3B82F6
 */
export const darkTheme: Partial<FullToken> = {
  // ── 主色 ──
  colorPrimary: '#3B82F6',
  colorPrimaryHover: '#60A5FA',
  colorPrimaryActive: '#2563EB',
  colorPrimaryBg: 'rgba(59, 130, 246, 0.15)',
  colorPrimaryBgHover: 'rgba(59, 130, 246, 0.25)',
  colorPrimaryBorder: 'rgba(59, 130, 246, 0.4)',
  colorPrimaryBorderHover: 'rgba(59, 130, 246, 0.6)',
  colorPrimaryText: '#93C5FD',
  colorPrimaryTextHover: '#60A5FA',
  colorPrimaryTextActive: '#3B82F6',

  // ── 背景 ──
  colorBgLayout: '#0a0e17',
  colorBgContainer: 'rgba(22, 27, 34, 0.8)',
  colorBgElevated: '#1a1f2e',
  colorBgSpotlight: '#1e2433',
  colorBgMask: 'rgba(0, 0, 0, 0.6)',

  // ── 边框 ──
  colorBorder: 'rgba(255, 255, 255, 0.08)',
  colorBorderSecondary: 'rgba(255, 255, 255, 0.04)',

  // ── 文字 ──
  colorText: '#e6edf3',
  colorTextSecondary: '#8b949e',
  colorTextTertiary: '#6e7681',
  colorTextQuaternary: '#484f58',

  // ── 填充 ──
  colorFill: 'rgba(255, 255, 255, 0.06)',
  colorFillSecondary: 'rgba(255, 255, 255, 0.04)',
  colorFillTertiary: 'rgba(255, 255, 255, 0.02)',
  colorFillQuaternary: 'rgba(255, 255, 255, 0.01)',
  colorFillAlter: '#161b22',
  colorFillContent: '#0d1117',
  colorFillContentHover: '#1c2129',

  // ── 状态色 ──
  colorSuccess: '#34D399',
  colorSuccessHover: '#6EE7B7',
  colorSuccessActive: '#059669',
  colorWarning: '#FBBF24',
  colorWarningHover: '#FCD34D',
  colorWarningActive: '#D97706',
  colorError: '#F87171',
  colorErrorHover: '#FCA5A5',
  colorErrorActive: '#DC2626',
  colorInfo: '#3B82F6',

  // ── 控件 ──
  controlItemBgHover: 'rgba(255, 255, 255, 0.06)',
  controlItemBgActive: 'rgba(59, 130, 246, 0.12)',

  // ── 自定义 Token ──
  colorBgGradient: 'linear-gradient(180deg, #0A0E17 0%, #111827 100%)',
  colorSiderBg: '#0c1119',
  colorHeaderBg: 'rgba(12, 17, 25, 0.95)',
  boxShadowCard: '0 1px 3px rgba(0, 0, 0, 0.4), 0 1px 2px rgba(0, 0, 0, 0.3)',
  boxShadowElevated:
    '0 10px 25px rgba(0, 0, 0, 0.5), 0 4px 10px rgba(0, 0, 0, 0.4)',
};

import type { FullToken } from './tokens';

/**
 * 亮色主题 Token
 *
 * 设计思路：
 * - 浅色背景 #F8FAFC → #F1F5F9
 * - 纯白卡片容器
 * - colorPrimary: 深蓝 #2563EB
 */
export const lightTheme: Partial<FullToken> = {
  // ── 主色 ──
  colorPrimary: '#2563EB',
  colorPrimaryHover: '#3B82F6',
  colorPrimaryActive: '#1D4ED8',
  colorPrimaryBg: '#EFF6FF',
  colorPrimaryBgHover: '#DBEAFE',
  colorPrimaryBorder: '#BFDBFE',
  colorPrimaryBorderHover: '#93C5FD',
  colorPrimaryText: '#2563EB',
  colorPrimaryTextHover: '#3B82F6',
  colorPrimaryTextActive: '#1D4ED8',

  // ── 背景 ──
  colorBgLayout: '#f8fafc',
  colorBgContainer: '#ffffff',
  colorBgElevated: '#ffffff',
  colorBgSpotlight: '#f8fafc',
  colorBgMask: 'rgba(0, 0, 0, 0.45)',

  // ── 边框 ──
  colorBorder: '#e2e8f0',
  colorBorderSecondary: '#f1f5f9',

  // ── 文字 ──
  colorText: '#0f172a',
  colorTextSecondary: '#475569',
  colorTextTertiary: '#94a3b8',
  colorTextQuaternary: '#cbd5e1',

  // ── 填充 ──
  colorFill: 'rgba(0, 0, 0, 0.06)',
  colorFillSecondary: 'rgba(0, 0, 0, 0.04)',
  colorFillTertiary: 'rgba(0, 0, 0, 0.02)',
  colorFillQuaternary: 'rgba(0, 0, 0, 0.01)',
  colorFillAlter: '#f8fafc',
  colorFillContent: '#f1f5f9',
  colorFillContentHover: '#e2e8f0',

  // ── 状态色 ──
  colorSuccess: '#16a34a',
  colorSuccessHover: '#22c55e',
  colorSuccessActive: '#15803d',
  colorWarning: '#ea580c',
  colorWarningHover: '#f97316',
  colorWarningActive: '#c2410c',
  colorError: '#dc2626',
  colorErrorHover: '#ef4444',
  colorErrorActive: '#b91c1c',
  colorInfo: '#2563EB',

  // ── 控件 ──
  controlItemBgHover: '#f1f5f9',
  controlItemBgActive: '#DBEAFE',

  // ── 自定义 Token ──
  colorBgGradient: 'linear-gradient(180deg, #F8FAFC 0%, #F1F5F9 100%)',
  colorSiderBg: '#0c1119',
  colorHeaderBg: '#ffffff',
  boxShadowCard: '0 1px 3px rgba(0, 0, 0, 0.06), 0 1px 2px rgba(0, 0, 0, 0.04)',
  boxShadowElevated:
    '0 10px 25px rgba(0, 0, 0, 0.08), 0 4px 10px rgba(0, 0, 0, 0.05)',
};

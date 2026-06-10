import type { FullToken } from './tokens';

/**
 * Tellius 亮色主题 Token
 *
 * 设计理念：
 * - 清爽白灰背景 #F8FAFC → #F1F5F9
 * - 纯白玻璃卡片，精致阴影
 * - 主色: 宝蓝 #2563EB
 * - 渐变强调关键 UI 元素
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

  // ── 背景层次 ──
  colorBgLayout: '#F8FAFC',
  colorBgContainer: '#FFFFFF',
  colorBgElevated: '#FFFFFF',
  colorBgSpotlight: '#F8FAFC',
  colorBgMask: 'rgba(15, 23, 42, 0.4)',

  // ── 边框 ──
  colorBorder: '#E2E8F0',
  colorBorderSecondary: '#F1F5F9',

  // ── 文字层次 ──
  colorText: '#0F172A',
  colorTextSecondary: '#475569',
  colorTextTertiary: '#94A3B8',
  colorTextQuaternary: '#CBD5E1',

  // ── 填充 ──
  colorFill: 'rgba(0, 0, 0, 0.04)',
  colorFillSecondary: 'rgba(0, 0, 0, 0.02)',
  colorFillTertiary: 'rgba(0, 0, 0, 0.01)',
  colorFillQuaternary: 'rgba(0, 0, 0, 0.005)',
  colorFillAlter: '#F8FAFC',
  colorFillContent: '#F1F5F9',
  colorFillContentHover: '#E2E8F0',

  // ── 状态色 ──
  colorSuccess: '#10B981',
  colorSuccessHover: '#34D399',
  colorSuccessActive: '#059669',
  colorWarning: '#F59E0B',
  colorWarningHover: '#FBBF24',
  colorWarningActive: '#D97706',
  colorError: '#EF4444',
  colorErrorHover: '#F87171',
  colorErrorActive: '#DC2626',
  colorInfo: '#3B82F6',

  // ── 链接色 ──
  colorLink: '#2563EB',
  colorLinkHover: '#3B82F6',
  colorLinkActive: '#1D4ED8',

  // ── 控件 ──
  controlItemBgHover: '#F1F5F9',
  controlItemBgActive: '#DBEAFE',

  // ── 自定义 Token ──
  colorBgGradient: 'linear-gradient(180deg, #F8FAFC 0%, #EFF6FF 50%, #F1F5F9 100%)',
  colorHeroGradient: 'linear-gradient(135deg, #EFF6FF 0%, #F0F9FF 40%, #EEF2FF 100%)',
  colorSiderBg: '#0F172A',
  colorHeaderBg: 'rgba(255, 255, 255, 0.92)',
  boxShadowCard: '0 1px 3px rgba(0, 0, 0, 0.04), 0 1px 2px rgba(0, 0, 0, 0.03)',
  boxShadowElevated:
    '0 10px 25px rgba(0, 0, 0, 0.06), 0 4px 10px rgba(0, 0, 0, 0.04)',
  boxShadowModal:
    '0 20px 60px rgba(0, 0, 0, 0.12), 0 8px 20px rgba(0, 0, 0, 0.08)',
  colorPrimaryGlow: '0 0 20px rgba(37, 99, 235, 0.15), 0 0 40px rgba(37, 99, 235, 0.05)',
  gradientAccent: 'linear-gradient(135deg, #2563EB 0%, #4F46E5 100%)',
  gradientSecondary: 'linear-gradient(135deg, #4F46E5 0%, #7C3AED 100%)',
};
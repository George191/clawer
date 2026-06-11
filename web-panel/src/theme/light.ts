import type { FullToken } from './tokens';

/**
 * Neo4j Aura Console 亮色主题 Token
 */
export const lightTheme: Partial<FullToken> = {
  // ── 主色 ──
  colorPrimary: '#006FCC',
  colorPrimaryHover: '#018BFF',
  colorPrimaryActive: '#0059A3',
  colorPrimaryBg: '#E6F4FF',
  colorPrimaryBgHover: '#CCE9FF',
  colorPrimaryBorder: '#99D3FF',
  colorPrimaryBorderHover: '#66B8FF',
  colorPrimaryText: '#006FCC',
  colorPrimaryTextHover: '#018BFF',
  colorPrimaryTextActive: '#0059A3',

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
  colorInfo: '#018BFF',

  // ── 链接色 ──
  colorLink: '#006FCC',
  colorLinkHover: '#018BFF',
  colorLinkActive: '#0059A3',

  // ── 控件 ──
  controlItemBgHover: '#F1F5F9',
  controlItemBgActive: '#E6F4FF',

  // ── 自定义 Token ──
  colorBgGradient: 'linear-gradient(180deg, #F8FAFC 0%, #EFF6FF 50%, #F1F5F9 100%)',
  colorHeroGradient: 'linear-gradient(135deg, #EFF6FF 0%, #F0F9FF 40%, #EEF2FF 100%)',
  colorSiderBg: '#F8FAFC',
  colorHeaderBg: '#FFFFFF',
  boxShadowCard: '0 1px 3px rgba(0, 0, 0, 0.04), 0 1px 2px rgba(0, 0, 0, 0.03)',
  boxShadowElevated:
    '0 10px 25px rgba(0, 0, 0, 0.06), 0 4px 10px rgba(0, 0, 0, 0.04)',
  boxShadowModal:
    '0 20px 60px rgba(0, 0, 0, 0.12), 0 8px 20px rgba(0, 0, 0, 0.08)',
  colorPrimaryGlow: '0 0 20px rgba(0, 111, 204, 0.15), 0 0 40px rgba(0, 111, 204, 0.05)',
  gradientAccent: 'linear-gradient(135deg, #006FCC 0%, #004D99 100%)',
  gradientSecondary: 'linear-gradient(135deg, #004D99 0%, #003366 100%)',
};
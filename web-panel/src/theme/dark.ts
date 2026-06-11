import type { FullToken } from './tokens';

/**
 * Neo4j Aura Console 暗色主题 Token
 *
 * 设计理念：
 * - 深空蓝黑背景 #12141A → #1A1D27，复刻 Neo4j Aura Console
 * - 侧边栏更深色 #0D0F14，形成层次对比
 * - 主色: Neo4j 品牌蓝 #018BFF
 * - 半透明玻璃拟态卡片，backdrop-blur 模糊
 * - 发光效果和渐变强调关键交互区域
 */
export const darkTheme: Partial<FullToken> = {
  // ── 主色 (Neo4j Blue) ──
  colorPrimary: '#018BFF',
  colorPrimaryHover: '#33A1FF',
  colorPrimaryActive: '#006FCC',
  colorPrimaryBg: 'rgba(1, 139, 255, 0.12)',
  colorPrimaryBgHover: 'rgba(1, 139, 255, 0.2)',
  colorPrimaryBorder: 'rgba(1, 139, 255, 0.35)',
  colorPrimaryBorderHover: 'rgba(1, 139, 255, 0.55)',
  colorPrimaryText: '#33A1FF',
  colorPrimaryTextHover: '#66B8FF',
  colorPrimaryTextActive: '#018BFF',

  // ── 背景层次 (Neo4j Aura Console) ──
  colorBgLayout: '#1A1D27',
  colorBgContainer: '#242836',
  colorBgElevated: '#2A2E3A',
  colorBgSpotlight: '#303548',
  colorBgMask: 'rgba(0, 0, 0, 0.65)',

  // ── 边框 ──
  colorBorder: 'rgba(255, 255, 255, 0.08)',
  colorBorderSecondary: 'rgba(255, 255, 255, 0.05)',

  // ── 文字层次 ──
  colorText: '#E4E7EB',
  colorTextSecondary: '#9CA3AF',
  colorTextTertiary: '#6B7280',
  colorTextQuaternary: '#4B5563',

  // ── 填充 ──
  colorFill: 'rgba(255, 255, 255, 0.04)',
  colorFillSecondary: 'rgba(255, 255, 255, 0.025)',
  colorFillTertiary: 'rgba(255, 255, 255, 0.015)',
  colorFillQuaternary: 'rgba(255, 255, 255, 0.008)',
  colorFillAlter: '#1E2030',
  colorFillContent: '#161821',
  colorFillContentHover: '#1E2030',

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
  colorLink: '#33A1FF',
  colorLinkHover: '#66B8FF',
  colorLinkActive: '#018BFF',

  // ── 控件 ──
  controlItemBgHover: 'rgba(255, 255, 255, 0.05)',
  controlItemBgActive: 'rgba(1, 139, 255, 0.12)',

  // ── 自定义 Token ──
  colorBgGradient: 'linear-gradient(180deg, #12141A 0%, #1A1D27 50%, #1E2230 100%)',
  colorHeroGradient: 'linear-gradient(135deg, #1A1D27 0%, #1E2230 40%, #1A2535 100%)',
  colorSiderBg: '#0D0F14',
  colorHeaderBg: '#12141A',
  boxShadowCard: '0 1px 3px rgba(0, 0, 0, 0.4), 0 1px 2px rgba(0, 0, 0, 0.2)',
  boxShadowElevated:
    '0 10px 30px rgba(0, 0, 0, 0.5), 0 4px 12px rgba(0, 0, 0, 0.3)',
  boxShadowModal:
    '0 20px 60px rgba(0, 0, 0, 0.6), 0 8px 20px rgba(0, 0, 0, 0.4)',
  colorPrimaryGlow: '0 0 20px rgba(1, 139, 255, 0.25), 0 0 40px rgba(1, 139, 255, 0.08)',
  gradientAccent: 'linear-gradient(135deg, #018BFF 0%, #0060CC 100%)',
  gradientSecondary: 'linear-gradient(135deg, #0060CC 0%, #003D99 100%)',
};
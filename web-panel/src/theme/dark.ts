import type { FullToken } from './tokens';

/**
 * Tellius 暗色主题 Token
 *
 * 设计理念：
 * - 深空蓝黑背景 #080C14 → #0F172A，营造专业 AI 分析平台质感
 * - 半透明玻璃拟态卡片，backdrop-blur 模糊
 * - 主色: 宝蓝 #3B82F6，辅色: 蓝紫 #6366F1
 * - 发光效果和渐变强调关键交互区域
 */
export const darkTheme: Partial<FullToken> = {
  // ── 主色 (Tellius Blue) ──
  colorPrimary: '#3B82F6',
  colorPrimaryHover: '#60A5FA',
  colorPrimaryActive: '#2563EB',
  colorPrimaryBg: 'rgba(59, 130, 246, 0.12)',
  colorPrimaryBgHover: 'rgba(59, 130, 246, 0.2)',
  colorPrimaryBorder: 'rgba(59, 130, 246, 0.35)',
  colorPrimaryBorderHover: 'rgba(59, 130, 246, 0.55)',
  colorPrimaryText: '#93C5FD',
  colorPrimaryTextHover: '#60A5FA',
  colorPrimaryTextActive: '#3B82F6',

  // ── 背景层次 ──
  colorBgLayout: '#080C14',
  colorBgContainer: 'rgba(15, 23, 42, 0.7)',
  colorBgElevated: '#111827',
  colorBgSpotlight: '#1E293B',
  colorBgMask: 'rgba(0, 0, 0, 0.65)',

  // ── 边框 ──
  colorBorder: 'rgba(255, 255, 255, 0.07)',
  colorBorderSecondary: 'rgba(255, 255, 255, 0.04)',

  // ── 文字层次 ──
  colorText: '#F1F5F9',
  colorTextSecondary: '#94A3B8',
  colorTextTertiary: '#64748B',
  colorTextQuaternary: '#475569',

  // ── 填充 ──
  colorFill: 'rgba(255, 255, 255, 0.05)',
  colorFillSecondary: 'rgba(255, 255, 255, 0.03)',
  colorFillTertiary: 'rgba(255, 255, 255, 0.02)',
  colorFillQuaternary: 'rgba(255, 255, 255, 0.01)',
  colorFillAlter: '#0F172A',
  colorFillContent: '#0A0F1A',
  colorFillContentHover: '#1A2236',

  // ── 状态色 (Tellius 风) ──
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
  colorLink: '#60A5FA',
  colorLinkHover: '#93C5FD',
  colorLinkActive: '#3B82F6',

  // ── 控件 ──
  controlItemBgHover: 'rgba(255, 255, 255, 0.06)',
  controlItemBgActive: 'rgba(59, 130, 246, 0.15)',

  // ── 自定义 Token ──
  colorBgGradient: 'linear-gradient(180deg, #080C14 0%, #0F172A 50%, #111827 100%)',
  colorHeroGradient: 'linear-gradient(135deg, #1E293B 0%, #0F172A 40%, #1A1A3E 100%)',
  colorSiderBg: '#060B14',
  colorHeaderBg: 'rgba(8, 12, 20, 0.85)',
  boxShadowCard: '0 1px 3px rgba(0, 0, 0, 0.5), 0 1px 2px rgba(0, 0, 0, 0.3)',
  boxShadowElevated:
    '0 10px 30px rgba(0, 0, 0, 0.6), 0 4px 12px rgba(0, 0, 0, 0.4)',
  boxShadowModal:
    '0 20px 60px rgba(0, 0, 0, 0.7), 0 8px 20px rgba(0, 0, 0, 0.5)',
  colorPrimaryGlow: '0 0 20px rgba(59, 130, 246, 0.3), 0 0 40px rgba(59, 130, 246, 0.1)',
  gradientAccent: 'linear-gradient(135deg, #3B82F6 0%, #6366F1 100%)',
  gradientSecondary: 'linear-gradient(135deg, #6366F1 0%, #8B5CF6 100%)',
};
import type { AliasToken } from 'antd/es/theme/interface';

/** 扩展自定义 Design Token — Tellius 风格 */
export interface CustomToken {
  /** 页面渐变背景 */
  colorBgGradient: string;
  /** Hero 区域渐变 */
  colorHeroGradient: string;
  /** 侧边栏背景色 */
  colorSiderBg: string;
  /** 顶栏背景色 */
  colorHeaderBg: string;
  /** 顶栏高度 */
  headerHeight: number;
  /** 侧边栏展开宽度 */
  siderWidth: number;
  /** 侧边栏收起宽度 */
  siderCollapsedWidth: number;
  /** 卡片阴影 */
  boxShadowCard: string;
  /** 浮层卡片阴影 */
  boxShadowElevated: string;
  /** 模态框阴影 */
  boxShadowModal: string;
  /** 发光主色 — 用于按钮/选中态光晕 */
  colorPrimaryGlow: string;
  /** 强调渐变 1: blue→indigo */
  gradientAccent: string;
  /** 强调渐变 2: indigo→purple */
  gradientSecondary: string;
}

export type FullToken = AliasToken & CustomToken;

/** 共享基础 Token */
export const themeTokens: Partial<FullToken> = {
  // ── 字体 ──
  fontFamily: `'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, 'PingFang SC', 'Microsoft YaHei', sans-serif`,
  fontSize: 14,
  fontSizeLG: 16,
  fontSizeXL: 20,
  fontSizeHeading1: 36,
  fontSizeHeading2: 28,
  fontSizeHeading3: 22,
  fontSizeHeading4: 18,
  lineHeight: 1.6,
  lineHeightHeading1: 1.2,
  lineHeightHeading2: 1.25,
  lineHeightHeading3: 1.3,

  // ── 圆角 ──
  borderRadius: 8,
  borderRadiusLG: 12,
  borderRadiusSM: 6,
  borderRadiusXS: 4,

  // ── 间距 ──
  paddingLG: 24,
  paddingMD: 16,
  paddingSM: 12,
  paddingXS: 8,
  marginLG: 24,
  marginMD: 16,
  marginSM: 12,
  marginXS: 8,

  // ── 控件 ──
  controlHeight: 38,
  controlHeightLG: 46,
  controlHeightSM: 30,

  wireframe: false,

  // ── 尺寸 ──
  headerHeight: 64,
  siderWidth: 248,
  siderCollapsedWidth: 80,
};

/** 状态色 — 统一亮/暗 */
export const statusColors = {
  success: '#10B981',
  warning: '#F59E0B',
  error: '#EF4444',
  info: '#3B82F6',
  processing: '#6366F1',
  default: '#94A3B8',
  pending: '#F59E0B',
};

/** 层级色 — 用于 ETL 拓扑图 */
export const layerColors: Record<string, string> = {
  Crawl: '#3B82F6',
  RDS: '#8B5CF6',
  ODS: '#06B6D4',
  TASK: '#10B981',
  DWD: '#F59E0B',
  DWS: '#EC4899',
  ADS: '#EF4444',
};

/** 图表调色板 */
export const chartPalette = [
  '#3B82F6',
  '#10B981',
  '#F59E0B',
  '#8B5CF6',
  '#EC4899',
  '#06B6D4',
  '#F97316',
  '#EF4444',
  '#6366F1',
  '#14B8A6',
];
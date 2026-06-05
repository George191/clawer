import type { AliasToken } from 'antd/es/theme/interface';

/** 扩展自定义 Design Token */
export interface CustomToken {
  /** 深色/浅色渐变背景 */
  colorBgGradient: string;
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
}

/** 完整 Token 类型 = antd AliasToken + 自定义扩展 */
export type FullToken = AliasToken & CustomToken;

/** 共享基础 Token（不依赖主题色） */
export const themeTokens: Partial<FullToken> = {
  // ── 字体 ──
  fontFamily: `-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, 'Noto Sans', 'PingFang SC', 'Microsoft YaHei', sans-serif`,
  fontSize: 14,
  lineHeight: 1.5715,

  // ── 圆角 ──
  borderRadius: 6,
  borderRadiusLG: 8,
  borderRadiusSM: 4,

  // ── 间距 ──
  paddingLG: 24,
  paddingMD: 16,
  paddingSM: 12,
  paddingXS: 8,

  // ── 控件 ──
  controlHeight: 36,
  controlHeightLG: 44,
  controlHeightSM: 28,

  wireframe: false,

  // ── 尺寸 ──
  headerHeight: 64,
  siderWidth: 240,
  siderCollapsedWidth: 80,
};

/** 状态色（亮/暗通用） */
export const statusColors = {
  success: '#52c41a',
  warning: '#faad14',
  error: '#ff4d4f',
  info: '#1677ff',
  processing: '#1677ff',
  default: '#d9d9d9',
};

/** 层级色 — 用于 ETL 拓扑图各节点 */
export const layerColors: Record<string, string> = {
  Crawl: '#1677ff',
  RDS: '#722ed1',
  ODS: '#13c2c2',
  TASK: '#52c41a',
  DWD: '#fa8c16',
  DWS: '#eb2f96',
  ADS: '#f5222d',
};

/** 图表调色板 */
export const chartPalette = [
  '#1677ff',
  '#52c41a',
  '#fa8c16',
  '#722ed1',
  '#eb2f96',
  '#13c2c2',
  '#faad14',
  '#f5222d',
];

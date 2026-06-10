/**
 * 非主题 Token — 图表调色板 / 层级色 / 状态色
 *
 * Design Token V2 语义色（亮/暗自适应）已全部迁移至 tokens.css，
 * 此处仅保留需要程序化操作（alpha 合成 / ECharts）的 hex 常量。
 *
 * ⚠️ 所有组件 inline styles 应优先使用 CSS 变量 var(--theme-*)，
 *    仅在 ECharts 配置 / alpha 合成 / gradient 等 JS 场景使用以下 hex。
 */

/** 状态枚举色标签（供 StatusBadge 等组件使用） */
export const statusColors = {
  success: 'var(--theme-color-success-text)',
  warning: 'var(--theme-color-warning-text)',
  error: 'var(--theme-color-danger-text)',
  info: 'var(--theme-color-discovery-text)',
  processing: 'var(--theme-color-primary-text)',
  default: 'var(--theme-color-neutral-text-weaker)',
};

/**
 * 语义色 Hex 值（仅用于 ECharts / alpha合成 / gradient 等 JS 场景）
 * - primary: Baltic 青色系（浅色主题 #0a6190）
 * - success/danger/warning/discovery: 对应 NDL 语义色
 */
export const semanticHex = {
  primary: '#0a6190',
  success: '#3f7824',
  danger: '#bb2d00',
  warning: '#765500',
  discovery: '#5a34aa',
  neutral: '#6f757e',
};

/** 层级色 — 用于 ETL 拓扑图各节点（视觉区分色，非语义化） */
export const layerColors: Record<string, string> = {
  Crawl: '#1677ff',
  RDS: '#722ed1',
  ODS: '#13c2c2',
  TASK: '#52c41a',
  DWD: '#fa8c16',
  DWS: '#eb2f96',
  ADS: '#f5222d',
};

/** 图表调色板（用于 ECharts series 颜色分配） */
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

/**
 * 任务/管道状态色（用于 ECharts 节点 / 状态指示线）
 * - running → primary
 * - done/completed → success
 * - error/failed → danger
 * - paused → warning
 * - stopped/idle → neutral
 */
export const statusHexMap = {
  running: semanticHex.primary,
  done: semanticHex.success,
  completed: semanticHex.success,
  error: semanticHex.danger,
  failed: semanticHex.danger,
  paused: semanticHex.warning,
  stopped: semanticHex.neutral,
  idle: semanticHex.neutral,
};

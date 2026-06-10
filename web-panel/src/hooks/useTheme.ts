import { useEffect, useState } from 'react';
import { useThemeStore } from '@/stores/settings';
import type { ThemeMode } from '@/stores/settings';
export type { ThemeMode };

/**
 * 主题 hook — 同步 data-theme + 监听系统偏好变化
 *
 * 注意：初始化 data-theme 由 index.html 内联脚本在首帧渲染前完成，
 *      此 hook 负责 React 生命周期的持续同步。
 */
export function useTheme() {
  const { mode, setMode, cycle } = useThemeStore();
  const [systemPrefersDark, setSystemPrefersDark] = useState(
    () => window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false,
  );

  // 同步 data-theme（React 接管后确保与 store 一致）
  useEffect(() => {
    const resolved = mode === 'system'
      ? (systemPrefersDark ? 'dark' : 'light')
      : mode;
    document.documentElement.setAttribute('data-theme', resolved);
  }, [mode, systemPrefersDark]);

  // 监听系统主题变化
  useEffect(() => {
    const mq = window.matchMedia('(prefers-color-scheme: dark)');
    const handler = (e: MediaQueryListEvent) => setSystemPrefersDark(e.matches);
    mq.addEventListener('change', handler);
    return () => mq.removeEventListener('change', handler);
  }, []);

  // 首次挂载时同步 store 值到 data-theme
  useEffect(() => {
    const resolved = mode === 'system'
      ? (systemPrefersDark ? 'dark' : 'light')
      : mode;
    document.documentElement.setAttribute('data-theme', resolved);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return { mode: mode as ThemeMode, setMode, cycle, systemPrefersDark };
}

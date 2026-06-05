import { useEffect, useState } from 'react';
import { useThemeStore, type ThemeMode } from '@/stores/settings';

/**
 * 主题 hook — 监听系统偏好 + 同步 store
 */
export function useTheme() {
  const { mode, toggle, setMode } = useThemeStore();
  const [systemPrefersDark, setSystemPrefersDark] = useState(
    () => window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? true,
  );

  useEffect(() => {
    const mq = window.matchMedia('(prefers-color-scheme: dark)');
    const handler = (e: MediaQueryListEvent) => setSystemPrefersDark(e.matches);
    mq.addEventListener('change', handler);
    return () => mq.removeEventListener('change', handler);
  }, []);

  return { mode: mode as ThemeMode, toggle, setMode, systemPrefersDark };
}
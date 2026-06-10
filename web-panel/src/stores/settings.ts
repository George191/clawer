import { create } from 'zustand';
import { persist } from 'zustand/middleware';

export type ThemeMode = 'dark' | 'light' | 'system';

interface SettingsState {
  mode: ThemeMode;
  setMode: (mode: ThemeMode) => void;
  /** 循环切换：dark → light → system → dark */
  cycle: () => void;
}

const CYCLE: ThemeMode[] = ['dark', 'light', 'system'];

export const useThemeStore = create<SettingsState>()(
  persist(
    (set, get) => ({
      mode: 'system',
      setMode: (mode) => {
        const resolved = mode === 'system'
          ? (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
          : mode;
        document.documentElement.setAttribute('data-theme', resolved);
        set({ mode });
      },
      cycle: () => {
        const { mode } = get();
        const idx = CYCLE.indexOf(mode);
        const next = CYCLE[(idx + 1) % CYCLE.length];
        const resolved = next === 'system'
          ? (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
          : next;
        document.documentElement.setAttribute('data-theme', resolved);
        set({ mode: next });
      },
    }),
    { name: 'claw-theme' },
  ),
);

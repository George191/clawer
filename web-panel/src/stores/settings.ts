import { create } from 'zustand';
import { persist } from 'zustand/middleware';

export type ThemeMode = 'dark' | 'light';
export type SystemLanguage = 'zh-CN' | 'en-US';

interface SettingsState {
  mode: ThemeMode;
  language: SystemLanguage;
  setMode: (mode: ThemeMode) => void;
  setLanguage: (language: SystemLanguage) => void;
  toggle: () => void;
}

export const useThemeStore = create<SettingsState>()(
  persist(
    (set) => ({
      mode: 'dark',
      language: 'zh-CN',
      setMode: (mode) => set({ mode }),
      setLanguage: (language) => set({ language }),
      toggle: () => set((s) => ({ mode: s.mode === 'dark' ? 'light' : 'dark' })),
    }),
    { name: 'etl-panel-settings' },
  ),
);

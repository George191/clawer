import { create } from 'zustand';
import type { DashboardMetrics, Alert } from '@/services/types';
import { fetchDashboardMetrics, fetchDashboardAlerts } from '@/services/api';

interface DashboardStore {
  metrics: DashboardMetrics | null;
  alerts: Alert[];
  loading: boolean;
  error: string | null;

  fetchMetrics: () => Promise<void>;
  fetchAlerts: () => Promise<void>;
}

export const useDashboardStore = create<DashboardStore>((set) => ({
  metrics: null,
  alerts: [],
  loading: false,
  error: null,

  fetchMetrics: async () => {
    set({ loading: true, error: null });
    try {
      const metrics = await fetchDashboardMetrics();
      set({ metrics, loading: false });
    } catch (e: unknown) {
      const err = e as { message?: string };
      set({ loading: false, error: err?.message || '获取仪表盘数据失败' });
    }
  },

  fetchAlerts: async () => {
    try {
      const alerts = await fetchDashboardAlerts();
      set({ alerts });
    } catch (e: unknown) {
      const err = e as { message?: string };
      set({ error: err?.message || '获取告警失败' });
    }
  },
}));

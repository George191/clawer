import { create } from 'zustand';
import type { DashboardMetrics, Alert } from '@/services/types';
import { fetchDashboardMetrics, fetchDashboardAlerts } from '@/services/api';

interface DashboardStore {
  metrics: DashboardMetrics | null;
  alerts: Alert[];
  loading: boolean;
  error: string | null;
  updatedAt: string | null;

  fetchMetrics: () => Promise<void>;
  fetchAlerts: () => Promise<void>;
  applySnapshot: (metrics: DashboardMetrics, alerts: Alert[], updatedAt: string) => void;
}

let metricsRequest: Promise<void> | null = null;
let alertsRequest: Promise<void> | null = null;

export const useDashboardStore = create<DashboardStore>((set) => ({
  metrics: null,
  alerts: [],
  loading: false,
  error: null,
  updatedAt: null,

  fetchMetrics: () => {
    if (metricsRequest) return metricsRequest;

    metricsRequest = (async () => {
      set({ loading: true, error: null });
      try {
        const metrics = await fetchDashboardMetrics();
        set({ metrics, loading: false });
      } catch (e: unknown) {
        const err = e as { message?: string };
        set({ loading: false, error: err?.message || '获取仪表盘数据失败' });
      } finally {
        metricsRequest = null;
      }
    })();
    return metricsRequest;
  },

  fetchAlerts: () => {
    if (alertsRequest) return alertsRequest;

    alertsRequest = (async () => {
      try {
        const alerts = await fetchDashboardAlerts();
        set({ alerts });
      } catch (e: unknown) {
        const err = e as { message?: string };
        set({ error: err?.message || '获取告警失败' });
      } finally {
        alertsRequest = null;
      }
    })();
    return alertsRequest;
  },

  applySnapshot: (metrics, alerts, updatedAt) => set({
    metrics,
    alerts,
    updatedAt,
    loading: false,
    error: null,
  }),
}));

import { create } from 'zustand';
import type { TaskInfo, TemplateInfo } from '@/services/types';
import { fetchTasks, fetchTemplates, runTask as apiRunTask, deleteTask as apiDeleteTask } from '@/services/api';

interface TasksStore {
  tasks: TaskInfo[];
  templates: TemplateInfo[];
  loading: boolean;
  error: string | null;

  fetchTasks: () => Promise<void>;
  fetchTemplates: () => Promise<void>;
  runTask: (id: string) => Promise<void>;
  deleteTask: (id: string) => Promise<void>;
}

export const useTasksStore = create<TasksStore>((set, get) => ({
  tasks: [],
  templates: [],
  loading: false,
  error: null,

  fetchTasks: async () => {
    set({ loading: true, error: null });
    try {
      const tasks = await fetchTasks();
      set({ tasks, loading: false });
    } catch (e: unknown) {
      const err = e as { message?: string };
      set({ loading: false, error: err?.message || '获取任务列表失败' });
    }
  },

  fetchTemplates: async () => {
    try {
      const templates = await fetchTemplates();
      set({ templates });
    } catch (e: unknown) {
      const err = e as { message?: string };
      set({ error: err?.message || '获取模板列表失败' });
    }
  },

  runTask: async (id: string) => {
    // optimistic update
    set((s) => ({
      tasks: s.tasks.map((t) =>
        t.id === id
          ? { ...t, status: 'running' as const, progress: 0, startedAt: new Date().toISOString(), duration: '0m 0s' }
          : t,
      ),
    }));
    try {
      await apiRunTask(id);
    } catch (e: unknown) {
      const err = e as { message?: string };
      set({
        error: err?.message || '启动任务失败',
        tasks: get().tasks.map((t) => (t.id === id ? { ...t, status: 'failed' as const } : t)),
      });
      throw e;
    }
  },

  deleteTask: async (id: string) => {
    const prev = get().tasks;
    set((s) => ({ tasks: s.tasks.filter((t) => t.id !== id) }));
    try {
      await apiDeleteTask(id);
    } catch (e: unknown) {
      set({ tasks: prev });
      const err = e as { message?: string };
      throw new Error(err?.message || '删除任务失败');
    }
  },
}));
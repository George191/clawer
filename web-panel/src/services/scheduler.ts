import client from './api';
import type { ProductDomain, SchedulerTaskConfig, WorkspaceTask } from './types';

export interface AutomationSchedule extends Omit<SchedulerTaskConfig, 'id'> {
  id?: number | string;
  workspace_task_id?: string;
  display_name?: string;
  schedule_label?: string;
}

export const automationFetchSchedules = (): Promise<SchedulerTaskConfig[]> =>
  client.get('/scheduler/tasks').then((r) => r.data);

export const fetchWorkspaceSchedules = (): Promise<AutomationSchedule[]> =>
  client.get<{ items: WorkspaceTask[] }>('/scheduler/workspace-tasks').then(({ data }) =>
    mapWorkspaceSchedules(data.items),
  );

export const mapWorkspaceSchedules = (tasks: WorkspaceTask[]): AutomationSchedule[] =>
  tasks.filter((task) => {
    const mode = task.schedule.mode === 'recurring' ? task.schedule.recurring_mode : task.schedule.mode;
    return mode === 'daily' || mode === 'interval';
  }).map((task) => {
      const schedule = task.schedule;
      const mode = schedule.mode === 'recurring' ? schedule.recurring_mode : schedule.mode;
      const [hour, minute] = String(schedule.daily_time ?? '09:00').split(':');
      const interval = Number(schedule.interval_value ?? 30);
      const hours = schedule.interval_unit === 'hour';
      return {
        id: task.id,
        workspace_task_id: task.id,
        display_name: task.name,
        task_name: task.id,
        task_path: task.template_name,
        product_domain: String(task.parameters?.product_domain ?? 'ai-collect') as ProductDomain,
        description: `${task.template_name}@${task.template_version}`,
        schedule_type: mode === 'interval' ? 'interval' : 'crontab',
        cron_minute: minute,
        cron_hour: hour,
        cron_day_of_week: '*', cron_day_of_month: '*', cron_month_of_year: '*',
        interval_seconds: interval * (hours ? 3600 : 60),
        schedule_label: mode === 'interval'
          ? `Every ${interval} ${hours ? 'hours' : 'minutes'}`
          : `Daily at ${hour}:${minute} (Asia/Shanghai)`,
        args: [], kwargs: {}, options: {},
        enabled: !['paused', 'completed'].includes(task.status) && task.control_state !== 'canceled',
        created_at: task.created_at, updated_at: task.updated_at,
      };
  });

export const automationCreateSchedule = (body: Omit<SchedulerTaskConfig, 'id' | 'created_at' | 'updated_at'>): Promise<SchedulerTaskConfig> =>
  client.post('/scheduler/tasks', body).then((r) => r.data);

export const automationUpdateSchedule = (name: string, body: Partial<SchedulerTaskConfig>): Promise<SchedulerTaskConfig> =>
  client.put(`/scheduler/tasks/${encodeURIComponent(name)}`, body).then((r) => r.data);

export const automationToggleSchedule = (name: string, enabled: boolean): Promise<SchedulerTaskConfig> =>
  client.post(`/scheduler/tasks/${encodeURIComponent(name)}/toggle`, { enabled }).then((r) => r.data);

export const automationDeleteSchedule = (name: string): Promise<void> =>
  client.delete(`/scheduler/tasks/${encodeURIComponent(name)}`).then(() => undefined);

export const automationReloadSchedules = (): Promise<{ loaded: boolean; task_count: number; restart_required: boolean; message: string }> =>
  client.post('/scheduler/reload').then((r) => r.data);

import client from './api';
import type { ProductDomain, SchedulerTaskConfig, WorkspaceTask } from './types';
import { formatWorkspaceScheduleLabel } from './taskSchedule';

export const intervalUnitSeconds = { minute: 60, hour: 3600, day: 86400, week: 604800, month: 2592000, second: 1 };
export type IntervalUnit = keyof typeof intervalUnitSeconds;

export interface AutomationSchedule extends Omit<SchedulerTaskConfig, 'id'> {
  id?: number | string;
  workspace_task_id?: string;
  workspace_task_canceled?: boolean;
  workspace_task_status?: WorkspaceTask['status'];
  workspace_task_owner?: string;
  display_name?: string;
  schedule_label?: string;
  interval_value?: number;
  interval_unit?: IntervalUnit;
}

export const getScheduleInterval = (schedule: AutomationSchedule) => {
  if (schedule.interval_value != null && schedule.interval_unit) {
    return { interval_value: schedule.interval_value, interval_unit: schedule.interval_unit };
  }
  const seconds = schedule.interval_seconds ?? 60;
  const unit = (['month', 'week', 'day', 'hour', 'minute', 'second'] as const).find((value) => seconds % intervalUnitSeconds[value] === 0) ?? 'second';
  return { interval_value: seconds / intervalUnitSeconds[unit], interval_unit: unit };
};

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
      const requestedUnit = String(schedule.interval_unit ?? 'minute');
      const unit = (requestedUnit !== 'second' && requestedUnit in intervalUnitSeconds ? requestedUnit : 'minute') as Exclude<IntervalUnit, 'second'>;
      return {
        id: task.id,
        workspace_task_id: task.id,
        workspace_task_canceled: task.control_state === 'canceled',
        workspace_task_status: task.status,
        workspace_task_owner: task.owner,
        display_name: task.name,
        task_name: task.id,
        task_path: task.task_path || task.template_name || '',
        product_domain: task.product_domain ?? String(task.parameters?.product_domain ?? 'ai-collect') as ProductDomain,
        description: task.task_type === 'celery'
          ? task.task_path ?? task.name
          : `${task.template_name ?? ''}@${task.template_version ?? ''}`,
        schedule_type: mode === 'interval' ? 'interval' : 'crontab',
        cron_minute: minute,
        cron_hour: hour,
        cron_day_of_week: '*', cron_day_of_month: '*', cron_month_of_year: '*',
        interval_value: interval,
        interval_unit: unit,
        interval_seconds: interval * intervalUnitSeconds[unit],
        schedule_label: formatWorkspaceScheduleLabel(schedule),
        args: [], kwargs: {}, options: {},
        enabled: task.control_state !== 'canceled' && (typeof schedule.enabled === 'boolean'
          ? schedule.enabled : !['paused', 'completed'].includes(task.status)),
        created_at: task.created_at, updated_at: task.updated_at,
      };
  });

export const automationUpdateWorkspaceSchedule = (taskId: string, body: {
  enabled?: boolean;
  mode?: 'daily' | 'interval';
  daily_time?: string;
  interval_value?: number;
  interval_unit?: Exclude<IntervalUnit, 'second'>;
}): Promise<AutomationSchedule> =>
  client.patch<WorkspaceTask>(`/scheduler/workspace-tasks/${encodeURIComponent(taskId)}`, body)
    .then(({ data }) => mapWorkspaceSchedules([data])[0]);

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

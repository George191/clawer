import client from './api';
import type { WorkspaceTask, WorkspaceTaskRequest } from './types';

export const fetchRuns = (): Promise<WorkspaceTask[]> =>
  client.get('/tasks/runs').then((r) => r.data.items);

export const createRun = (body: WorkspaceTaskRequest): Promise<WorkspaceTask> =>
  client.post('/tasks/runs', body).then((r) => r.data);

export const runAction = (id: string, action: string): Promise<WorkspaceTask> =>
  client.post(`/tasks/runs/${id}/action`, { action }).then((r) => r.data);

export const deleteRun = (id: string): Promise<void> =>
  client.delete(`/tasks/runs/${id}`).then(() => undefined);

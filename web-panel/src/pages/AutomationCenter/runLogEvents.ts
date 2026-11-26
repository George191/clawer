import type { WorkspaceTaskLog } from '@/services/aiApi';
import { isOlderWorkerNotice, latestWorkerNotice, parseWorkerNotice } from '@/pages/AICollect/workerNotices';

export const noticeLog = (log: WorkspaceTaskLog) => ({ message: log.message, createdAt: log.created_at, runId: log.run_id });
export function mergeRunLogs(current: WorkspaceTaskLog[], incoming: WorkspaceTaskLog[]): WorkspaceTaskLog[] {
  const events = new Map(current.map(log => [String(log.id), log]));
  for (const log of incoming) {
    const existing = events.get(String(log.id));
    if (!existing || !isOlderWorkerNotice(noticeLog(log), noticeLog(existing))) events.set(String(log.id), log);
  }
  return [...events.values()].sort((a, b) => Date.parse(a.created_at) - Date.parse(b.created_at) || String(a.id).length - String(b.id).length || String(a.id).localeCompare(String(b.id)));
}
export function runLogPresentation(logs: WorkspaceTaskLog[], runId: string | undefined) {
  const selected = logs.filter(log => log.run_id === runId);
  return {
    lines: selected.filter(log => !parseWorkerNotice(noticeLog(log))),
    sync: latestWorkerNotice(selected.map(noticeLog), runId || null, 'sync'),
    download: latestWorkerNotice(selected.map(noticeLog), runId || null, 'download'),
  };
}

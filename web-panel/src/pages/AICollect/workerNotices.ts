type NoticeKind = 'sync' | 'download';
type NoticeStatus = 'syncing' | 'sent' | 'failed' | 'pending_retry' | 'preparing' | 'downloading' | 'complete' | 'no_assets' | 'claim_lost' | 'cancelled' | 'historical' | 'waiting' | 'warning';

interface NoticeLog {
  message: string;
  createdAt: string;
  runId: string;
}

export interface WorkerNotice {
  kind: NoticeKind;
  recordId?: string;
  topic?: string;
  itemsCompleted?: number;
  itemsTotal?: number;
  itemsMissing?: number;
  itemsFailed?: number;
  status: NoticeStatus;
  updatedAt: number;
}

const prefixPattern = /^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}): ([A-Z]+)\/([^\]]+)\]\s*/;
const syncPattern = /^(?:RecordPublisher:|SyncWorker\b|sync progress:|Kafka publish acknowledged:|Sent \d+\/\d+ records to Kafka|Failed to send (?:Kafka message|record):|Failed to connect to Kafka:|Kafka not ready |Topic \S+ not found, waiting for auto-creation|Connected to Kafka:|Kafka producer closed)/;
const downloadPattern = /^(?:DownloadWorker\b|Download uploaded:|Download failed:|Downloading bytes:|Download completed but batch counter update failed:|Download task log write failed:|download (?:progress|finished|stalled):|HTTP .* during download cancellation)/;
const statuses = new Set<NoticeStatus>(['syncing', 'sent', 'failed', 'pending_retry', 'preparing', 'downloading', 'complete', 'no_assets', 'claim_lost', 'cancelled', 'historical', 'waiting', 'warning']);

export const parseWorkerNotice = (log: NoticeLog): WorkerNotice | null => {
  const prefix = log.message.match(prefixPattern);
  const body = prefix ? log.message.slice(prefix[0].length) : log.message;
  const actor = prefix?.[3];
  const kind = actor === 'Syncer' || syncPattern.test(body) ? 'sync'
    : actor === 'Downloader' || downloadPattern.test(body) ? 'download' : null;
  if (!kind) return null;
  const value = (name: string) => body.match(new RegExp(`\\b${name}=([^\\s;|]+)`))?.[1];
  const count = (name: string) => {
    const item = value(name);
    return item != null && /^\d+$/.test(item) ? Number(item) : undefined;
  };
  const oldSent = body.match(/^RecordPublisher: sent → (\S+) record_id=(\S+)/);
  const oldFailed = body.match(/^(?:RecordPublisher: sync failed for|DownloadWorker failed for) (?!record_id=)(\S+)/);
  const recordId = value('record_id') ?? value('record') ?? oldFailed?.[1];
  const topic = value('topic') ?? oldSent?.[1];
  const declaredStatus = value('status');
  let status: NoticeStatus = declaredStatus && statuses.has(declaredStatus as NoticeStatus)
    ? declaredStatus as NoticeStatus : 'waiting';
  if (declaredStatus === 'downloaded') status = 'complete';
  if (status === 'waiting') {
    if (/progress logging failed|statistics failed|batch counter update failed|task log write failed/.test(body)) status = 'warning';
    else if (/claim no longer matches|claim lost/.test(body)) status = 'claim_lost';
    else if (/timed out|stalled|retry/i.test(body)) status = 'pending_retry';
    else if (prefix?.[2] === 'ERROR' || prefix?.[2] === 'CRITICAL' || /\bfailed\b/i.test(body)) status = 'failed';
    else if (prefix?.[2] === 'WARNING') status = 'warning';
    else if (/historical summary/.test(body)) status = 'historical';
    else if (kind === 'sync' && /sent →|(?:publish |Kafka )acknowledged|Sent \d+\/\d+/.test(body)) status = 'sent';
    else if (kind === 'download' && /download finished:|record completed/.test(body)) status = 'complete';
    else if (kind === 'download' && /progress:|uploaded:/.test(body)) status = 'downloading';
    else if (kind === 'sync' && /sync progress:/.test(body)) status = 'syncing';
  }
  let itemsCompleted = count('items_completed');
  let itemsTotal = count('items_total');
  let itemsMissing = count('items_missing');
  let itemsFailed = count('items_failed');
  if (kind === 'download' && /^DownloadWorker: record completed/.test(body)) {
    const downloaded = count('downloaded');
    const reused = count('reused');
    itemsMissing = count('not_found');
    itemsFailed = count('failed');
    if ([downloaded, reused, itemsMissing, itemsFailed].every((item) => item != null)) {
      itemsCompleted = downloaded! + reused!;
      itemsTotal = itemsCompleted + itemsMissing! + itemsFailed!;
    }
  }
  const eventTime = prefix ? Date.parse(`${prefix[1].replace(' ', 'T').replace(',', '.')}Z`) : NaN;
  return {
    kind, recordId, topic, itemsCompleted, itemsTotal, itemsMissing, itemsFailed, status,
    updatedAt: Number.isFinite(eventTime) ? eventTime : Date.parse(log.createdAt) || 0,
  };
};

export const isOlderWorkerNotice = (incoming: NoticeLog, existing: NoticeLog): boolean => {
  const next = parseWorkerNotice(incoming);
  const previous = parseWorkerNotice(existing);
  return Boolean(next && previous && next.updatedAt < previous.updatedAt);
};

export const latestWorkerNotice = (logs: NoticeLog[], runId: string | null, kind: NoticeKind): WorkerNotice | null => {
  let latest: WorkerNotice | null = null;
  let detailed: WorkerNotice | null = null;
  for (const log of logs) {
    if (log.runId !== runId) continue;
    const notice = parseWorkerNotice(log);
    if (notice?.kind !== kind) continue;
    if (!latest || notice.updatedAt >= latest.updatedAt) latest = notice;
    if (notice.recordId && (!detailed || notice.updatedAt >= detailed.updatedAt)) detailed = notice;
  }
  // Legacy batch summaries and HTTP asset updates lack record identity; keep
  // the latest record details rather than replacing them with those summaries.
  if (detailed && latest && !['failed', 'pending_retry', 'claim_lost', 'cancelled', 'warning'].includes(latest.status)) return detailed;
  return latest;
};

export const workerNoticeStatusLabel = (status: NoticeStatus): string => ({
  syncing: 'Syncing', sent: 'Sent', failed: 'Failed', pending_retry: 'Retry pending',
  preparing: 'Preparing', downloading: 'Downloading', complete: 'Complete',
  no_assets: 'No assets', claim_lost: 'Claim lost', cancelled: 'Cancelled',
  historical: 'Historical', waiting: 'Waiting', warning: 'Warning',
}[status]);

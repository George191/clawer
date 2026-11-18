import { useEffect, useRef, useState } from 'react';
import { Button, Modal } from '@/components/ui';
import { fetchMonthlyCommittedRun } from '@/services/api';
import type { MonthlyCommittedRun as Run, TransferScheduleJob } from '@/services/api';
import { isUtcMonthDay } from '@/services/scheduleTime';
import { getSessionState } from '@/services/session';
import { canonicalTransferReceipt } from '../Pipeline/transferReceiptFocus';
import { UpstreamUtcTime } from '../Pipeline/UpstreamSlotHistory';

type MonthlyJob = TransferScheduleJob & { receiptId: string; scheduleDayOfMonthUtc: number };

export function isCommittedMonthlyJob(job: { slot: string; id: string; state: string; receiptId: string | null; scheduleDayOfMonthUtc?: unknown }): job is MonthlyJob {
  return job.state === 'committed' && typeof job.receiptId === 'string' && canonicalTransferReceipt(job.receiptId) &&
    typeof job.scheduleDayOfMonthUtc === 'number' && isUtcMonthDay(job.scheduleDayOfMonthUtc);
}

const identifier = (value: string) => typeof value === 'string' && /^[a-z_][a-z0-9_]{0,62}$/.test(value);

export default function MonthlyCommittedRun({ planId, job, identity, disabled }: {
  planId: string; job: MonthlyJob; identity: string; disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [result, setResult] = useState<Run>();
  const [unavailable, setUnavailable] = useState(false);
  const pending = useRef<AbortController>();
  useEffect(() => () => pending.current?.abort(), []);
  const live = (signal?: AbortSignal) => {
    const state = getSessionState();
    return !signal?.aborted && state.authenticated && !state.locked && state.identity === identity;
  };
  const cancel = () => {
    pending.current?.abort(); pending.current = undefined;
    setOpen(false); setResult(undefined); setUnavailable(false);
  };
  const inspect = async () => {
    if (pending.current || disabled || !live()) return;
    const read = new AbortController(); pending.current = read;
    setOpen(true); setResult(undefined); setUnavailable(false);
    try {
      const value = await fetchMonthlyCommittedRun(planId, job.id, read.signal);
      if (!live(read.signal)) return;
      if (value.kind !== 'monthly_utc' || value.planId !== planId || value.jobId !== job.id || value.receiptId !== job.receiptId ||
        value.slot !== job.slot || !Number.isFinite(Date.parse(value.slot)) || value.scheduleDayOfMonthUtc !== job.scheduleDayOfMonthUtc ||
        value.sourceSchema !== 'ts_transfer_sandbox' || value.targetSchema !== 'ts_transfer_sandbox' ||
        value.sourceTable === value.targetTable || ![value.sourceTable, value.targetTable].every(identifier) ||
        !Number.isSafeInteger(value.rowsAffected) || value.rowsAffected < 0 || !Number.isFinite(Date.parse(value.recordedAt))) {
        setUnavailable(true); return;
      }
      setResult(value);
    } catch {
      if (live(read.signal)) setUnavailable(true);
    } finally {
      if (pending.current === read) pending.current = undefined;
    }
  };
  return <>
    <Button size="small" disabled={disabled} onClick={() => void inspect()}>View exact run</Button>
    <Modal open={open} destroyOnHidden className="flow-surface-modal" title="Exact monthly run" footer={null} onCancel={cancel}>
      {unavailable ? <p role="status">Exact run details are unavailable.</p> : !result ? <p role="status">Verifying committed monthly run…</p> : <>
        <strong role="status">Verified committed monthly run</strong>
        <p><code>{result.sourceSchema}.{result.sourceTable}</code> → <code>{result.targetSchema}.{result.targetTable}</code></p>
        <dl className="flow-evidence-freshness-facts">
          <div><dt>Transfer plan ID</dt><dd><code>{result.planId}</code></dd></div>
          <div><dt>Job ID</dt><dd><code>{result.jobId}</code></dd></div>
          <div><dt>Receipt ID</dt><dd><code>{result.receiptId}</code></dd></div>
          <div><dt>Day of month (UTC)</dt><dd>{result.scheduleDayOfMonthUtc}</dd></div>
          <div><dt>UTC slot</dt><dd><UpstreamUtcTime value={result.slot} /></dd></div>
          <div><dt>Receipt recorded at (UTC)</dt><dd><UpstreamUtcTime value={result.recordedAt} /></dd></div>
          <div><dt>Rows affected</dt><dd>{result.rowsAffected.toLocaleString()}</dd></div>
        </dl>
        {result.rowsAffected === 0 && <p>Committed with zero affected rows. This does not show data movement.</p>}
      </>}
    </Modal>
  </>;
}

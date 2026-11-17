import { useEffect, useRef, useState } from 'react';
import { Modal } from '@/components/ui';
import { ArrowRightOutlined } from '@/components/ui/icons';
import { fetchTransferUpstreamCandidates, fetchTransferUpstreamJobs, fetchTransferUpstreamTrigger, updateTransferUpstreamTrigger } from '../../services/api';
import type { TransferPlan, TransferUpstreamCandidate, TransferUpstreamJob, TransferUpstreamTrigger } from '../../services/api';
import { getSessionState } from '../../services/session';

const labels: Record<TransferUpstreamJob['state'], string> = {
  pending: 'Pending dispatch', dispatched: 'Dispatched', committed: 'Committed', rejected: 'Rejected',
  outcome_unknown: 'Outcome unknown', expired: 'Expired',
};
const utcText = (value: string) => new Date(value).toLocaleString(undefined, { timeZone: 'UTC', timeZoneName: 'short' });

export default function LineageUpstreamTrigger({ plan, maxRows, formatError }: {
  plan: TransferPlan; maxRows: number; formatError: (failure: unknown) => string;
}) {
  const [binding, setBinding] = useState<TransferUpstreamTrigger>();
  const [candidates, setCandidates] = useState<TransferUpstreamCandidate[]>();
  const [jobs, setJobs] = useState<TransferUpstreamJob[]>();
  const [parentId, setParentId] = useState('');
  const [bindingError, setBindingError] = useState('');
  const [candidatesError, setCandidatesError] = useState('');
  const [jobsError, setJobsError] = useState('');
  const [mutationError, setMutationError] = useState('');
  const [confirm, setConfirm] = useState<'arm' | 'pause'>();
  const [version, setVersion] = useState(0);
  const [busy, setBusy] = useState(false);
  const pending = useRef(false);
  const active = useRef(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const identity = useRef(getSessionState().identity).current;
  const live = (signal?: AbortSignal) => {
    const session = getSessionState();
    return active.current && !signal?.aborted && session.authenticated && !session.locked && session.identity === identity;
  };

  useEffect(() => { active.current = true; heading.current?.focus(); return () => { active.current = false; }; }, []);
  useEffect(() => {
    const read = new AbortController();
    setBinding(undefined); setCandidates(undefined); setJobs(undefined);
    setBindingError(''); setCandidatesError(''); setJobsError('');
    void fetchTransferUpstreamTrigger(plan.id, read.signal).then(value => {
      if (live(read.signal)) { setBinding(value); setParentId(value.parentPlanId ?? ''); }
    }).catch(failure => { if (live(read.signal)) setBindingError(formatError(failure)); });
    void fetchTransferUpstreamCandidates(plan.id, read.signal).then(value => {
      if (live(read.signal)) setCandidates(value);
    }).catch(failure => { if (live(read.signal)) setCandidatesError(formatError(failure)); });
    void fetchTransferUpstreamJobs(plan.id, read.signal).then(value => {
      if (live(read.signal)) setJobs(value);
    }).catch(failure => { if (live(read.signal)) setJobsError(formatError(failure)); });
    return () => read.abort();
  }, [plan.id, version, identity, formatError]);

  const selected = candidates?.find(candidate => candidate.id === parentId);
  const canArm = Boolean(binding?.available && !binding.configuredEnabled && selected);
  const submit = async () => {
    if (!binding || !confirm || pending.current || !live()) return;
    const enabled = confirm === 'arm';
    if (enabled ? !canArm || !selected : !binding.configuredEnabled) return;
    pending.current = true; setBusy(true); setMutationError('');
    try {
      await updateTransferUpstreamTrigger(plan.id, {
        enabled,
        parentPlanId: enabled && selected ? selected.id : null,
        expectedParentUpdatedAt: enabled && selected ? selected.updatedAt : null,
        expectedUpdatedAt: plan.updatedAt,
        expectedGeneration: binding.generation,
      });
    } catch (failure) {
      if (live()) setMutationError(`${formatError(failure)} The registered binding is being refreshed.`);
    } finally {
      pending.current = false;
      if (live()) { setConfirm(undefined); setBusy(false); setVersion(value => value + 1); }
    }
  };

  return <section className="flow-transfer-schedule flow-transfer-upstream" aria-label="Upstream trigger">
    <div className="flow-transfer-heading"><h4 ref={heading} tabIndex={-1}>Upstream trigger</h4>
      <button type="button" className="flow-lineage-action" disabled={busy} onClick={() => { setMutationError(''); setVersion(value => value + 1); }}>Refresh trigger</button></div>
    <p>Bind a manual producer whose destination is this plan’s exact sandbox source table. Only eligible commits recorded after arming can dispatch this child.</p>
    {mutationError && <p role="alert" className="flow-transfer-error">{mutationError}</p>}
    {bindingError ? <p role="alert" className="flow-transfer-error">{bindingError}</p> : !binding ? <p role="status">Loading upstream trigger…</p> : <>
      <strong role="status">{binding.configuredEnabled ? binding.enabled && binding.available ? 'Upstream trigger armed' : 'Upstream trigger armed · blocked' : binding.generation == null ? 'Upstream trigger not armed' : 'Upstream trigger paused'}</strong>
      {!binding.available && <p>Upstream execution is unavailable for this plan or environment.</p>}
      {binding.parentPlanName && <p>Bound parent: <strong>{binding.parentPlanName}</strong></p>}
      {binding.armedForParentUpdatedAt && <p>Bound parent revision: <time dateTime={binding.armedForParentUpdatedAt}>{utcText(binding.armedForParentUpdatedAt)}</time></p>}
      {binding.armedForUpdatedAt && <p>Bound child revision: <time dateTime={binding.armedForUpdatedAt}>{utcText(binding.armedForUpdatedAt)}</time></p>}
      {binding.configuredEnabled
        ? <div className="flow-transfer-run-actions"><button type="button" className="flow-lineage-action" disabled={busy} onClick={() => setConfirm('pause')}>Pause upstream trigger</button></div>
        : <>
          <label>Upstream transfer plan <select value={parentId} disabled={busy || !binding.available || !candidates?.length} onChange={event => setParentId(event.target.value)}>
            <option value="">Select an eligible manual producer</option>
            {parentId && !selected && <option value={parentId} disabled>{binding.parentPlanName ?? parentId} (unavailable)</option>}
            {candidates?.map(item => <option key={item.id} value={item.id}>{item.name} · {item.sourceTable} → {item.targetTable}</option>)}
          </select></label>
          {candidatesError ? <p role="alert" className="flow-transfer-error">{candidatesError}</p> : !candidates ? <p role="status">Loading upstream producers…</p> :
            !candidates.length && <p>No eligible manual producer writes to this source table.</p>}
          <div className="flow-transfer-run-actions"><button type="button" className="flow-lineage-action is-primary" disabled={busy || !canArm} onClick={() => setConfirm('arm')}>Arm upstream trigger</button></div>
        </>}
    </>}
    <h4>Upstream jobs</h4>
    <p>A parent receipt can start dispatch. Only a separate child receipt verifies the child transfer.</p>
    {jobsError ? <p role="alert" className="flow-transfer-error">{jobsError}</p> : !jobs ? <p role="status">Loading upstream jobs…</p>
      : jobs.length ? <ol className="flow-transfer-receipts">{jobs.map(job => <li key={job.id}>
        <strong>{labels[job.state]}</strong>
        <div>Parent receipt: <code>{job.parentReceiptId}</code></div>
        <div>Parent event recorded: <time dateTime={job.eventRecordedAt}>{utcText(job.eventRecordedAt)}</time></div>
        {job.dispatchedAt && <div>Dispatched at: <time dateTime={job.dispatchedAt}>{utcText(job.dispatchedAt)}</time></div>}
        {job.finishedAt && <div>Finished at: <time dateTime={job.finishedAt}>{utcText(job.finishedAt)}</time></div>}
        {job.receiptId && <div>Child receipt: <code>{job.receiptId}</code></div>}
        {job.message && <p>{job.message}</p>}
      </li>)}</ol> : <p>No upstream jobs recorded.</p>}
    <Modal open={Boolean(confirm)} destroyOnHidden className="flow-surface-modal" title={confirm === 'pause' ? 'Pause sandbox upstream trigger?' : 'Arm sandbox upstream trigger?'}
      onCancel={() => setConfirm(undefined)} onOk={submit} confirmLoading={busy} okText={confirm === 'pause' ? 'Confirm pause' : 'Confirm arm trigger'}>
      {confirm === 'pause' ? <p>Stop future dispatches without reversing committed transfers. A transfer that has already started may still finish.</p> :
        <><p>Allow Automation to dispatch this saved child Upsert when the saved producer <strong>{selected?.name}</strong> commits an eligible run. A child run can write up to {maxRows} sandbox rows.</p>
          {selected && <p>Parent revision: <time dateTime={selected.updatedAt}>{utcText(selected.updatedAt)}</time> · Child revision: <time dateTime={plan.updatedAt}>{utcText(plan.updatedAt)}</time></p>}
          <div className="flow-transfer-route"><code>{plan.sourceSchema}.{plan.sourceTable}</code><ArrowRightOutlined /><code>{plan.targetSchema}.{plan.targetTable}</code></div></>}
    </Modal>
  </section>;
}

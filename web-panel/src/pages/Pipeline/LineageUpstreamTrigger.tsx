import { useEffect, useRef, useState } from 'react';
import { Modal, NativeSelect } from '@/components/ui';
import { ArrowRightOutlined } from '@/components/ui/icons';
import { fetchTransferUpstreamCandidates, fetchTransferUpstreamJobs, fetchTransferUpstreamTrigger, updateTransferUpstreamTrigger } from '../../services/api';
import type { TransferPlan, TransferUpstreamCandidate, TransferUpstreamGroup, TransferUpstreamJob, TransferUpstreamOperator, TransferUpstreamTrigger } from '../../services/api';
import { getSessionState } from '../../services/session';
import { oppositeOperator, recordedUpstreamRule, upstreamExpression } from './upstreamRules';
import UpstreamSlotHistory, { UpstreamUtcTime } from './UpstreamSlotHistory';

const labels: Record<TransferUpstreamJob['state'], string> = {
  pending: 'Pending dispatch', dispatched: 'Dispatched', committed: 'Committed', rejected: 'Rejected',
  outcome_unknown: 'Outcome unknown', expired: 'Expired',
};
const utcText = (value: string) => new Date(value).toLocaleString(undefined, { timeZone: 'UTC', timeZoneName: 'short' });
const maxParents = 3;
const utcClock = /^([01][0-9]|2[0-3]):[0-5][0-9]$/;

export default function LineageUpstreamTrigger({ plan, maxRows, formatError }: {
  plan: TransferPlan; maxRows: number; formatError: (failure: unknown) => string;
}) {
  const [binding, setBinding] = useState<TransferUpstreamTrigger>();
  const [candidates, setCandidates] = useState<TransferUpstreamCandidate[]>();
  const [jobs, setJobs] = useState<TransferUpstreamJob[]>();
  const [parentIds, setParentIds] = useState<string[]>([]);
  const [operator, setOperator] = useState<TransferUpstreamOperator>('OR');
  const [groupEnabled, setGroupEnabled] = useState(false);
  const [groupIds, setGroupIds] = useState<string[]>([]);
  const [gateEnabled, setGateEnabled] = useState(false);
  const [gateTime, setGateTime] = useState('');
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
      if (live(read.signal)) {
        setBinding(value);
        setParentIds(value.parents.map(parent => parent.planId));
        setOperator(value.operator);
        setGroupEnabled(Boolean(value.group));
        setGroupIds(value.group?.parentPlanIds ?? []);
        setGateEnabled(Boolean(value.mixedTimeUtc));
        setGateTime(value.mixedTimeUtc ?? '');
      }
    }).catch(failure => { if (live(read.signal)) setBindingError(formatError(failure)); });
    void fetchTransferUpstreamCandidates(plan.id, read.signal).then(value => {
      if (live(read.signal)) setCandidates(value);
    }).catch(failure => { if (live(read.signal)) setCandidatesError(formatError(failure)); });
    void fetchTransferUpstreamJobs(plan.id, read.signal).then(value => {
      if (live(read.signal)) setJobs(value);
    }).catch(failure => { if (live(read.signal)) setJobsError(formatError(failure)); });
    return () => read.abort();
  }, [plan.id, version, identity, formatError]);

  const selected = candidates?.filter(candidate => parentIds.includes(candidate.id)) ?? [];
  const unlisted = binding?.parents.filter(parent => parentIds.includes(parent.planId) &&
    candidates && !candidates.some(candidate => candidate.id === parent.planId)) ?? [];
  const group: TransferUpstreamGroup | undefined = groupEnabled && selected.length === maxParents && groupIds.length === 2 &&
    groupIds[0] !== groupIds[1] && groupIds.every(id => selected.some(parent => parent.id === id))
    ? { operator: oppositeOperator(operator), parentPlanIds: [groupIds[0], groupIds[1]] } : undefined;
  const expression = groupEnabled && !group ? undefined : upstreamExpression(operator, selected.map(parent => ({ id: parent.id, label: parent.name })), group);
  const canArm = Boolean(binding?.available && !binding.configuredEnabled && selected.length >= 1 &&
    selected.length === parentIds.length && parentIds.length <= maxParents && (!groupEnabled || group) &&
    (!gateEnabled || binding.mixedAvailable && utcClock.test(gateTime)));
  const toggleParent = (id: string, checked: boolean) => {
    setParentIds(previous => checked && !previous.includes(id) && previous.length < maxParents ? [...previous, id]
      : checked ? previous : previous.filter(item => item !== id));
    if (!checked) setGroupIds(previous => previous.filter(item => item !== id));
  };
  const toggleGroupMember = (id: string, checked: boolean) => setGroupIds(previous =>
    checked && !previous.includes(id) && previous.length < 2 ? [...previous, id]
      : checked ? previous : previous.filter(item => item !== id));
  const submit = async () => {
    if (!binding || !confirm || pending.current || !live()) return;
    const enabled = confirm === 'arm';
    if (enabled ? !canArm : !binding.configuredEnabled) return;
    pending.current = true; setBusy(true); setMutationError('');
    try {
      await updateTransferUpstreamTrigger(plan.id, {
        enabled,
        operator: enabled ? operator : binding.operator,
        ...(enabled && group ? { group } : {}),
        ...(enabled && gateEnabled ? { mixedTimeUtc: gateTime } : enabled && binding.mixedTimeUtc ? { mixedTimeUtc: null } : {}),
        parents: enabled ? selected.map(parent => ({ planId: parent.id, expectedUpdatedAt: parent.updatedAt })) : [],
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
    <p>Bind 1–{maxParents} manual producers whose destination is this plan’s exact sandbox source table. Only eligible commits recorded after arming can dispatch this child.</p>
    {mutationError && <p role="alert" className="flow-transfer-error">{mutationError}</p>}
    {bindingError ? <p role="alert" className="flow-transfer-error">{bindingError}</p> : !binding ? <p role="status">Loading upstream trigger…</p> : <>
      <strong role="status">{binding.configuredEnabled ? binding.enabled && binding.available ? 'Upstream trigger armed' : 'Upstream trigger armed · blocked' : binding.generation == null ? 'Upstream trigger not armed' : 'Upstream trigger paused'}</strong>
      {!binding.available && <p>Upstream execution is unavailable for this plan or environment.</p>}
      {binding.mixedTimeUtc && <p>Bound UTC time gate: <strong>{binding.mixedTimeUtc} UTC AND an eligible upstream witness</strong>.</p>}
      {binding.parents.length > 0 && <><p>Bound condition: <strong>{binding.group ? upstreamExpression(binding.operator, binding.parents.map(parent => ({
        id: parent.planId, label: parent.planName,
      })), binding.group) : binding.operator}</strong></p>
        <ul aria-label="Bound upstream parents" className="flow-transfer-receipts">{binding.parents.map(parent => <li key={parent.planId}>
          <strong>{parent.planName}</strong><span>Bound parent revision: <time dateTime={parent.armedForUpdatedAt}>{utcText(parent.armedForUpdatedAt)}</time></span>
        </li>)}</ul>
      </>}
      {binding.armedForUpdatedAt && <p>Bound child revision: <time dateTime={binding.armedForUpdatedAt}>{utcText(binding.armedForUpdatedAt)}</time></p>}
      {binding.configuredEnabled
        ? <div className="flow-transfer-run-actions"><button type="button" className="flow-lineage-action" disabled={busy} onClick={() => setConfirm('pause')}>Pause upstream trigger</button></div>
        : <>
          <label>Upstream condition <NativeSelect aria-label="Upstream condition" value={operator} disabled={busy || !binding.available} onChange={event => setOperator(event.target.value as TransferUpstreamOperator)}>
            <option value="OR">{groupEnabled ? 'OR · Either branch' : 'OR · Any producer commits'}</option>
            <option value="AND">{groupEnabled ? 'AND · Both branches' : 'AND · All producers commit'}</option>
          </NativeSelect></label>
          {!groupEnabled && !gateEnabled && <p>{operator === 'AND' ? 'Each job consumes one eligible, unused commit from every selected producer.' : 'Each eligible, unused producer commit can start its own job.'}</p>}
          {candidates && (candidates.length > 0 || unlisted.length > 0) && <fieldset className="flow-transfer-parent-options" disabled={busy || !binding.available}>
            <legend>Eligible manual producers ({parentIds.length}/{maxParents} selected)</legend>
            {unlisted.map(item => <label key={item.planId}><input type="checkbox" checked onChange={() => toggleParent(item.planId, false)} />{item.planName} (unavailable)</label>)}
            {candidates.map(item => <label key={item.id}><input type="checkbox" checked={parentIds.includes(item.id)}
              disabled={parentIds.length >= maxParents && !parentIds.includes(item.id)} onChange={event => toggleParent(item.id, event.target.checked)} />
              {item.name} · {item.sourceTable} → {item.targetTable}
            </label>)}
          </fieldset>}
          {(selected.length === maxParents || groupEnabled) && <fieldset className="flow-transfer-parent-options" disabled={busy || !binding.available}>
            <legend>Optional bracketed condition</legend>
            <label><input type="checkbox" checked={groupEnabled} onChange={event => {
              setGroupEnabled(event.target.checked);
              if (!event.target.checked) setGroupIds([]);
            }} />Group two producers</label>
            {groupEnabled && (selected.length !== maxParents ? <p>Select three eligible producers, or turn grouping off.</p> :
              <fieldset className="flow-transfer-parent-options">
                <legend>Producers inside parentheses</legend>
                {selected.map(parent => <label key={parent.id}><input type="checkbox" checked={groupIds.includes(parent.id)}
                  disabled={groupIds.length >= 2 && !groupIds.includes(parent.id)}
                  onChange={event => toggleGroupMember(parent.id, event.target.checked)} />{parent.name}</label>)}
                <p>Inside the parentheses: <strong>{oppositeOperator(operator)}</strong>. Outside: <strong>{operator}</strong>.</p>
              </fieldset>)}
          </fieldset>}
          {groupEnabled && <><p role="status">Condition preview: <strong>{expression ?? 'Select two producers for the group.'}</strong></p>
            <p>Only eligible, unused parent commits that satisfy this specific expression are consumed. Other eligible commits remain available.</p></>}
          {(binding.mixedAvailable || gateEnabled) && <fieldset className="flow-transfer-time-gate" disabled={busy || !binding.available}>
            <legend>Optional UTC time gate</legend>
            <label><input type="checkbox" checked={gateEnabled} onChange={event => setGateEnabled(event.target.checked)} />Require a UTC time slot</label>
            {gateEnabled && <>
              <label>Daily time gate (UTC) <input type="time" step={60} aria-label="Daily time gate (UTC)"
                value={gateTime} disabled={!binding.mixedAvailable} onChange={event => setGateTime(event.target.value)} /></label>
              <p role="status">Time gate preview: <strong>{utcClock.test(gateTime) ? `${gateTime} UTC` : 'Choose an exact HH:mm UTC time'} AND {expression && selected.length ? `(${expression})` : 'an eligible upstream witness'}.</strong></p>
            </>}
            <p>Automation checks for a qualifying witness in the last five minutes before the slot. Eligible commits may be earlier in the daily interval. Late observations do not backfill past slots, and a slot without a witness does not start a run.</p>
            {!binding.mixedAvailable && !binding.mixedConflict && <p>The saved time gate is unavailable here. Remove it explicitly to review an event-only condition.</p>}
          </fieldset>}
          {binding.mixedConflict && <p>The independent Daily UTC schedule is armed. Pause it before arming a UTC time gate on this trigger.</p>}
          {candidatesError ? <p role="alert" className="flow-transfer-error">{candidatesError}</p> : !candidates ? <p role="status">Loading upstream producers…</p> :
            !candidates.length && <p>No eligible manual producer writes to this source table.</p>}
          <div className="flow-transfer-run-actions"><button type="button" className="flow-lineage-action is-primary" disabled={busy || !canArm} onClick={() => setConfirm('arm')}>Arm upstream trigger</button></div>
        </>}
    </>}
    <h4>Upstream jobs</h4>
    <p>Parent receipts show which commits met the condition. Only a separate child receipt verifies the child transfer.</p>
    {jobsError ? <p role="alert" className="flow-transfer-error">{jobsError}</p> : !jobs ? <p role="status">Loading upstream jobs…</p>
      : jobs.length ? <ol className="flow-transfer-receipts">{jobs.map(job => {
        const recorded = recordedUpstreamRule(job);
        return <li key={job.id}>
          <strong>{labels[job.state]} · {recorded.expression}</strong>
          {job.mixedSlot && <><div>Recorded mode: UTC time gate AND upstream witness</div>
            <div>UTC slot: <UpstreamUtcTime value={job.mixedSlot} /></div>
            {job.witnessObservedAt && <div>Witness observed at: <UpstreamUtcTime value={job.witnessObservedAt} /></div>}
          </>}
          {recorded.parents.length > 0 && <ul aria-label="Recorded rule producers" className="flow-transfer-parent-events">{recorded.parents.map(parent => <li key={parent.id}>
            {parent.label}: <code>{parent.id}</code>
          </li>)}</ul>}
          <ul className="flow-transfer-parent-events" aria-label="Parent events">{job.events.map(event => <li key={event.eventId}>
            <div>Producer plan: <code>{event.parentPlanId}</code></div>
            <div>Parent receipt: <code>{event.parentReceiptId}</code></div>
            <div>Parent event recorded: <time dateTime={event.eventRecordedAt}>{utcText(event.eventRecordedAt)}</time></div>
          </li>)}</ul>
          {job.dispatchedAt && <div>Dispatched at: <time dateTime={job.dispatchedAt}>{utcText(job.dispatchedAt)}</time></div>}
          {job.finishedAt && <div>Finished at: <time dateTime={job.finishedAt}>{utcText(job.finishedAt)}</time></div>}
          {job.receiptId && <div>Child receipt: <code>{job.receiptId}</code></div>}
          {job.message && <p>{job.message}</p>}
        </li>;
      })}</ol> : <p>No upstream jobs recorded.</p>}
    {(binding?.mixedTimeUtc || binding?.mixedHasHistory || jobs?.some(job => job.mixedSlot)) &&
      <UpstreamSlotHistory planId={plan.id} version={version} />}
    <Modal open={Boolean(confirm)} destroyOnHidden className="flow-surface-modal" title={confirm === 'pause' ? 'Pause sandbox upstream trigger?' : 'Arm sandbox upstream trigger?'}
      onCancel={() => setConfirm(undefined)} onOk={submit} confirmLoading={busy} okText={confirm === 'pause' ? 'Confirm pause' : 'Confirm arm trigger'}>
      {confirm === 'pause' ? <p>Prevent queued jobs from starting new writes. Already running work may still finish.</p> :
        <>{group ? <p>Allow Automation to dispatch this saved child Upsert when eligible commits satisfy <strong>{expression}</strong>. Only matching commits are consumed. A child run can write up to {maxRows} sandbox rows.</p>
          : <p>Allow Automation to dispatch this saved child Upsert after <strong>{operator === 'AND' ? 'each selected producer' : 'any selected producer'}</strong> commits an eligible run. A child run can write up to {maxRows} sandbox rows.</p>}
          {gateEnabled && <p>Also require a daily slot at <strong>{gateTime} UTC</strong>. Automation checks for a qualifying witness in the five minutes before the slot; late observations do not backfill past slots.</p>}
          <ul>{selected.map(parent => <li key={parent.id}><strong>{parent.name}</strong> · Parent revision: <time dateTime={parent.updatedAt}>{utcText(parent.updatedAt)}</time></li>)}</ul>
          <p>Child revision: <time dateTime={plan.updatedAt}>{utcText(plan.updatedAt)}</time></p>
          <div className="flow-transfer-route"><code>{plan.sourceSchema}.{plan.sourceTable}</code><ArrowRightOutlined /><code>{plan.targetSchema}.{plan.targetTable}</code></div></>}
    </Modal>
  </section>;
}
